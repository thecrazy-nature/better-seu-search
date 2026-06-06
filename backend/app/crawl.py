from __future__ import annotations

from collections.abc import Callable

from .crawler.seu_sites import PublicSiteCrawler
from .storage import DocumentStore


def run_crawl(
    collection_id: int | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> int:
    store = DocumentStore()
    store.init_db()
    collection = store.get_collection(collection_id) if collection_id is not None else store.get_default_collection()
    if collection is None:
        raise ValueError("No collection is available for crawling.")
    sites = store.get_collection_crawl_sites(collection["id"])
    if not sites:
        raise ValueError("This collection has no enabled crawl sources.")

    if progress_callback:
        progress_callback(
            {
                "phase": "preparing",
                "message": f"Preparing crawl sources for {collection['name']}.",
                "progress_current": 0,
                "progress_total": sum(int(site.get("max_pages") or 0) for site in sites) or len(sites) or 1,
                "progress_percent": 0.02,
            }
        )

    crawler = PublicSiteCrawler(sites=sites, progress_callback=progress_callback)
    count = 0
    batch = []
    crawled_urls: list[str] = []

    def on_document(doc):
        nonlocal count
        batch.append(doc)
        crawled_urls.append(doc.url)
        if len(batch) >= 25:
            count += store.upsert_documents(batch)
            print(f"[store] upserted={count}", flush=True)
            batch.clear()
            if progress_callback:
                progress_callback(
                    {
                        "phase": "indexing",
                        "message": f"Indexed {count} documents while crawling {collection['name']}.",
                    }
                )

    crawler.crawl_all(on_document=on_document, verbose=True)
    if batch:
        count += store.upsert_documents(batch)
        print(f"[store] upserted={count}", flush=True)
        if progress_callback:
            progress_callback(
                {
                    "phase": "indexing",
                    "message": f"Indexed {count} documents while crawling {collection['name']}.",
                }
            )

    if progress_callback:
        progress_callback(
            {
                "phase": "finalizing",
                "message": f"Refreshing collection membership for {collection['name']}.",
                "progress_percent": 0.97,
            }
        )
    member_count = store.replace_collection_documents(collection["id"], crawled_urls)
    print(f"[store] collection_members={member_count}", flush=True)
    orphan_deleted = store.prune_orphan_documents()
    if orphan_deleted:
        print(f"[store] deleted_orphans={orphan_deleted}", flush=True)
    crawler.write_report()
    return count


if __name__ == "__main__":
    count = run_crawl()
    print(f"upserted {count} documents")
