from __future__ import annotations

from .backfill_calendar_2025_2026 import backfill_calendar
from .crawler.seu_sites import PublicSiteCrawler
from .storage import DocumentStore


def run_crawl() -> int:
    store = DocumentStore()
    store.init_db()
    crawler = PublicSiteCrawler()
    count = 0
    batch = []

    def on_document(doc):
        nonlocal count
        batch.append(doc)
        if len(batch) >= 25:
            count += store.upsert_documents(batch)
            print(f"[store] upserted={count}", flush=True)
            batch.clear()

    crawler.crawl_all(on_document=on_document, verbose=True)
    if batch:
        count += store.upsert_documents(batch)
        print(f"[store] upserted={count}", flush=True)
    skipped_urls = []
    for site in crawler.report["sites"].values():
        skipped_urls.extend(item["url"] for item in site.get("skipped_document_pages", []))
        skipped_urls.extend(site.get("list_pages", []))
    deleted = store.delete_documents_by_urls(skipped_urls)
    if deleted:
        print(f"[store] deleted_non_article={deleted}", flush=True)
    duplicate_deleted = store.delete_duplicate_documents()
    if duplicate_deleted:
        print(f"[store] deleted_duplicates={duplicate_deleted}", flush=True)
    calendar_result = backfill_calendar()
    print(f"[store] backfilled_calendar={calendar_result}", flush=True)
    crawler.write_report()
    return count


if __name__ == "__main__":
    count = run_crawl()
    print(f"upserted {count} documents")
