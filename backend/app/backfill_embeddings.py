from __future__ import annotations

import argparse

from .embeddings import embedding_signature
from .storage import DocumentStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--refresh", action="store_true", help="Regenerate embeddings even when a vector exists.")
    parser.add_argument(
        "--metadata",
        action="store_true",
        help="Refresh chunk tags/search text and regenerate stale embeddings.",
    )
    args = parser.parse_args()
    store = DocumentStore()
    store.init_db()
    if args.metadata:
        count = store.backfill_chunk_metadata(limit=args.limit, refresh_embeddings=args.refresh)
        print({"updated_chunk_metadata": count, "embedding": embedding_signature()})
    else:
        count = store.backfill_chunk_embeddings(limit=args.limit, refresh=args.refresh)
        print({"updated_chunks": count, "embedding": embedding_signature()})


if __name__ == "__main__":
    main()
