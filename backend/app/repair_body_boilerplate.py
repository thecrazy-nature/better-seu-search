from __future__ import annotations

import argparse
import json
import re
from typing import Any

from .content_cleaning import boilerplate_score, clean_body_text
from .crawler.seu_sites import PublicSiteCrawler
from .preprocess import extract_keywords
from .storage import DocumentStore


SUSPECT_TERMS = [
    "简体中文",
    "部门简介",
    "办事平台",
    "网站管理",
    "版权所有",
    "更多联系方式",
    "处室电话",
    "附件正文摘录",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes to the database. Default is dry-run.")
    parser.add_argument("--all", action="store_true", help="Scan every document, not only obvious boilerplate suspects.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sample", type=int, default=12)
    parser.add_argument(
        "--keep-ai-metadata",
        action="store_true",
        help="Do not clear ai_metadata_json on changed documents.",
    )
    args = parser.parse_args()

    store = DocumentStore()
    store.init_db()
    rows = _select_rows(store, limit=args.limit)

    candidates = []
    for row in rows:
        old_body = row["body"] or ""
        if not args.all and not _is_suspect(old_body):
            continue
        new_body = clean_body_text(old_body, title=row["title"])
        if _compact(new_body) == _compact(old_body):
            continue
        candidates.append((row, old_body, new_body))

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "scanned": len(rows),
            "candidates": len(candidates),
            "all": args.all,
        },
        flush=True,
    )

    for row, old_body, new_body in candidates[: max(0, args.sample)]:
        print(
            json.dumps(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "old_len": len(old_body),
                    "new_len": len(new_body),
                    "old_score": boilerplate_score(old_body),
                    "new_score": boilerplate_score(new_body),
                    "old_head": _preview(old_body),
                    "new_head": _preview(new_body),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    if not args.apply:
        return

    updated = 0
    for row, _old_body, new_body in candidates:
        attachments = _json_list(row["attachments_json"])
        attachment_text = "\n".join(str(item.get("text") or "") for item in attachments if isinstance(item, dict))
        text_for_tags = f"{row['title']}\n{new_body}\n{attachment_text}"
        store.update_document_clean_body(
            int(row["id"]),
            new_body,
            keywords=extract_keywords(row["title"], f"{new_body}\n{attachment_text}", attachments),
            topics=PublicSiteCrawler._extract_topics(text_for_tags),
            applicable_colleges=PublicSiteCrawler._extract_colleges(text_for_tags),
            applicable_grades=PublicSiteCrawler._extract_grades(text_for_tags),
            student_types=PublicSiteCrawler._extract_student_types(text_for_tags),
            deadline=PublicSiteCrawler._extract_deadline(text_for_tags),
            replace_deadline=True,
            clear_ai_metadata=not args.keep_ai_metadata,
        )
        updated += 1
        print(
            {
                "event": "body_cleaned",
                "updated": updated,
                "total": len(candidates),
                "id": row["id"],
                "title": row["title"],
                "old_len": len(_old_body),
                "new_len": len(new_body),
            },
            flush=True,
        )

    print({"updated": updated, "ai_metadata_cleared": updated if not args.keep_ai_metadata else 0}, flush=True)


def _select_rows(store: DocumentStore, limit: int | None) -> list[Any]:
    sql = "SELECT * FROM documents ORDER BY publish_date DESC NULLS LAST, id DESC"
    params: tuple[Any, ...] = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    with store.connect() as conn:
        return conn.execute(sql, params).fetchall()


def _is_suspect(body: str) -> bool:
    if not body:
        return False
    if boilerplate_score(body) >= 2:
        return True
    return sum(1 for term in SUSPECT_TERMS if term in body) >= 1


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _preview(text: str, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


if __name__ == "__main__":
    main()
