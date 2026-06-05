from __future__ import annotations

from .date_utils import publish_date_from_url
from .storage import DocumentStore


def repair_index_metadata() -> dict[str, int]:
    store = DocumentStore()
    store.init_db()
    deleted_demo_documents = 0
    deleted_orphan_chunks = 0
    fixed_publish_dates = 0
    fixed_chunk_dates = 0

    with store.connect() as conn:
        demo_rows = conn.execute("SELECT id FROM documents WHERE url LIKE ?", ("%/demo/%",)).fetchall()
        demo_ids = [row["id"] for row in demo_rows]
        if demo_ids:
            placeholders = ",".join("?" for _ in demo_ids)
            conn.execute(f"DELETE FROM document_chunks WHERE document_id IN ({placeholders})", demo_ids)
            cursor = conn.execute(f"DELETE FROM documents WHERE id IN ({placeholders})", demo_ids)
            deleted_demo_documents = cursor.rowcount if cursor.rowcount is not None else 0

        cursor = conn.execute(
            """
            DELETE FROM document_chunks
            WHERE document_id NOT IN (SELECT id FROM documents)
            """
        )
        deleted_orphan_chunks = cursor.rowcount if cursor.rowcount is not None else 0

        rows = conn.execute("SELECT id, url, publish_date FROM documents").fetchall()
        for row in rows:
            url_date = publish_date_from_url(row["url"])
            if not url_date:
                continue
            value = url_date.isoformat()
            if row["publish_date"] != value:
                conn.execute("UPDATE documents SET publish_date = ? WHERE id = ?", (value, row["id"]))
                fixed_publish_dates += 1
            cursor = conn.execute(
                """
                UPDATE document_chunks
                SET publish_date = ?
                WHERE document_id = ?
                  AND (publish_date IS NULL OR publish_date != ?)
                """,
                (value, row["id"], value),
            )
            fixed_chunk_dates += cursor.rowcount if cursor.rowcount is not None else 0

    return {
        "deleted_demo_documents": deleted_demo_documents,
        "deleted_orphan_chunks": deleted_orphan_chunks,
        "fixed_publish_dates": fixed_publish_dates,
        "fixed_chunk_dates": fixed_chunk_dates,
    }


if __name__ == "__main__":
    print(repair_index_metadata())
