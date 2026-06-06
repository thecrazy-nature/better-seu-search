from __future__ import annotations

import argparse

import httpx

from .attachments import attachment_extension, extract_attachment_payload
from .storage import DocumentStore, _json_load_list


MAX_FILE_BYTES = 8 * 1024 * 1024
SUPPORTED_EXTENSIONS = {"", ".pdf", ".doc", ".docx", ".xls", ".xlsx"}
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/vnd.openxmlformats-officedocument.*,*/*;q=0.8",
}


def backfill_attachment_text(
    limit: int | None = None,
    timeout: float = 20,
    refresh_legacy: bool = False,
) -> dict[str, int]:
    store = DocumentStore()
    store.init_db()
    stats = {
        "documents": 0,
        "updated": 0,
        "attachments": 0,
        "pages": 0,
        "sheets": 0,
        "tables": 0,
        "table_rows": 0,
        "skipped_existing": 0,
        "skipped_unsupported": 0,
        "skipped_too_large": 0,
        "download_failed": 0,
        "request_error": 0,
        "http_404": 0,
        "http_error": 0,
        "parse_empty": 0,
        "refreshed_existing": 0,
        "cleared_existing": 0,
    }
    rows = store.iter_documents_with_attachments(limit)

    with httpx.Client(timeout=timeout, follow_redirects=True, headers=DEFAULT_HEADERS) as client:
        for row in rows:
            stats["documents"] += 1
            attachments = _json_load_list(row["attachments_json"])
            changed = False
            for item in attachments:
                url = item.get("url", "")
                ext = attachment_extension(url, item.get("name", ""))
                should_refresh = refresh_legacy and ext in {".doc", ".xls"}
                if item.get("text") and not should_refresh:
                    stats["skipped_existing"] += 1
                    continue
                if ext not in SUPPORTED_EXTENSIONS:
                    stats["skipped_unsupported"] += 1
                    continue
                try:
                    response = client.get(url, headers={"Referer": row["url"]})
                except httpx.RequestError:
                    stats["download_failed"] += 1
                    stats["request_error"] += 1
                    continue
                if response.status_code == 404:
                    stats["download_failed"] += 1
                    stats["http_404"] += 1
                    continue
                if response.status_code >= 400:
                    stats["download_failed"] += 1
                    stats["http_error"] += 1
                    continue
                if len(response.content) > MAX_FILE_BYTES:
                    stats["skipped_too_large"] += 1
                    continue
                payload = extract_attachment_payload(
                    response.content,
                    url,
                    item.get("name", ""),
                    response.headers.get("content-type", ""),
                )
                if payload.get("text"):
                    item["text"] = payload["text"]
                    item.pop("pages", None)
                    item.pop("sheets", None)
                    item.pop("tables", None)
                    if payload.get("pages"):
                        item["pages"] = payload["pages"]
                        stats["pages"] += len(payload["pages"])
                    if payload.get("sheets"):
                        item["sheets"] = payload["sheets"]
                        stats["sheets"] += len(payload["sheets"])
                        stats["table_rows"] += sum(len(sheet.get("rows") or []) for sheet in payload["sheets"])
                    if payload.get("tables"):
                        item["tables"] = payload["tables"]
                        stats["tables"] += len(payload["tables"])
                        stats["table_rows"] += sum(len(table.get("rows") or []) for table in payload["tables"])
                    changed = True
                    if should_refresh:
                        stats["refreshed_existing"] += 1
                    stats["attachments"] += 1
                else:
                    if should_refresh and item.get("text"):
                        item.pop("text", None)
                        item.pop("pages", None)
                        item.pop("sheets", None)
                        item.pop("tables", None)
                        changed = True
                        stats["cleared_existing"] += 1
                    stats["parse_empty"] += 1
            if changed:
                store.update_document_attachments(row["id"], attachments)
                stats["updated"] += 1
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument(
        "--refresh-legacy",
        action="store_true",
        help="Re-parse existing .doc/.xls attachments and clear low-confidence legacy text.",
    )
    args = parser.parse_args()
    print(backfill_attachment_text(limit=args.limit, timeout=args.timeout, refresh_legacy=args.refresh_legacy))


if __name__ == "__main__":
    main()
