from __future__ import annotations

import argparse
import json
import re
import time
from typing import Any

from bs4 import BeautifulSoup

from .content_cleaning import clean_body_text
from .crawler.seu_sites import PublicSiteCrawler
from .storage import DocumentStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write rescued content to the database.")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--sample", type=int, default=12)
    parser.add_argument("--max-old-body-len", type=int, default=40)
    parser.add_argument("--min-new-body-len", type=int, default=80)
    parser.add_argument("--min-attachment-text-len", type=int, default=80)
    parser.add_argument("--request-delay", type=float, default=0.2)
    parser.add_argument("--ids", default="", help="Comma-separated document ids to repair.")
    args = parser.parse_args()

    store = DocumentStore()
    store.init_db()
    crawler = PublicSiteCrawler(max_pages_per_site=1, delay_seconds=0)
    rows = _select_rows(store, args)

    rescued: list[dict[str, Any]] = []
    skipped = 0
    failed = 0
    updated = 0

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "selected": len(rows),
            "max_old_body_len": args.max_old_body_len,
            "min_new_body_len": args.min_new_body_len,
            "min_attachment_text_len": args.min_attachment_text_len,
        },
        flush=True,
    )

    for index, row in enumerate(rows, start=1):
        old_body = row["body"] or ""
        result: dict[str, Any] = {
            "index": index,
            "id": row["id"],
            "title": row["title"],
            "url": row["url"],
            "old_len": len(old_body),
        }
        try:
            response = crawler._get(row["url"])
            if response is None or "text/html" not in response.headers.get("content-type", ""):
                failed += 1
                result["status"] = "fetch_failed"
                _print_sample(result, rescued, args.sample)
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            doc = crawler._parse_document(soup, row["url"], row["source"])
            if doc is None:
                skipped += 1
                result["status"] = "no_document"
                result["html_len"] = len(response.text)
                _print_sample(result, rescued, args.sample)
                continue

            doc.body = clean_body_text(doc.body, title=doc.title)
            attachment_text_len = sum(len(str(item.get("text") or "")) for item in doc.attachments)
            is_rescuable = (
                len(doc.body) >= args.min_new_body_len
                or attachment_text_len >= args.min_attachment_text_len
            )
            result.update(
                {
                    "status": "candidate",
                    "new_title": doc.title,
                    "new_len": len(doc.body),
                    "attachment_count": len(doc.attachments),
                    "attachment_text_len": attachment_text_len,
                    "new_head": _preview(doc.body),
                }
            )
            if not is_rescuable:
                skipped += 1
                result["status"] = "too_short"
                _print_sample(result, rescued, args.sample)
                continue

            if len(doc.body) < args.min_new_body_len:
                result["status"] = "attachment_only"
            rescued.append(result)
            _print_sample(result, rescued, args.sample)
            if args.apply:
                store.upsert_documents([doc])
                updated += 1
                print(
                    {
                        "event": "document_rescued",
                        "updated": updated,
                        "id": row["id"],
                        "title": row["title"],
                        "old_len": len(old_body),
                        "new_len": len(doc.body),
                        "attachment_count": len(doc.attachments),
                        "attachment_text_len": attachment_text_len,
                    },
                    flush=True,
                )
        except Exception as exc:
            failed += 1
            result["status"] = "error"
            result["message"] = str(exc)[:300]
            _print_sample(result, rescued, args.sample)
        finally:
            if args.request_delay > 0:
                time.sleep(args.request_delay)

    print(
        {
            "selected": len(rows),
            "rescuable": len(rescued),
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "dry_run": not args.apply,
        },
        flush=True,
    )


def _select_rows(store: DocumentStore, args: argparse.Namespace) -> list[Any]:
    ids = [int(item) for item in re.findall(r"\d+", args.ids or "")]
    params: list[Any] = []
    sql = "SELECT * FROM documents"
    if ids:
        placeholders = ",".join("?" for _ in ids)
        sql += f" WHERE id IN ({placeholders})"
        params.extend(ids)
    else:
        sql += " WHERE LENGTH(COALESCE(body, '')) <= ?"
        params.append(max(0, args.max_old_body_len))
    sql += " ORDER BY publish_date DESC NULLS LAST, id DESC"
    if args.offset:
        sql += " OFFSET ?"
        params.append(max(0, args.offset))
    with store.connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    if ids:
        return rows[: args.limit] if args.limit else rows
    unresolved = [
        row
        for row in rows
        if _stored_attachment_text_len(row["attachments_json"]) < args.min_attachment_text_len
    ]
    return unresolved[: args.limit] if args.limit else unresolved


def _print_sample(result: dict[str, Any], rescued: list[dict[str, Any]], sample_limit: int) -> None:
    if len(rescued) <= max(0, sample_limit) or result.get("status") not in {"too_short", "no_document"}:
        print(json.dumps(result, ensure_ascii=False), flush=True)


def _stored_attachment_text_len(value: str | None) -> int:
    if not value:
        return 0
    try:
        attachments = json.loads(value)
    except json.JSONDecodeError:
        return 0
    if not isinstance(attachments, list):
        return 0
    total = 0
    for item in attachments:
        if not isinstance(item, dict):
            continue
        total += len(str(item.get("text") or ""))
        for page in item.get("pages") or []:
            if isinstance(page, dict):
                total += len(str(page.get("text") or ""))
        for sheet in item.get("sheets") or []:
            if isinstance(sheet, dict):
                total += len(str(sheet.get("text") or ""))
    return total


def _preview(text: str, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


if __name__ == "__main__":
    main()
