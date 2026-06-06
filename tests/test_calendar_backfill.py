from __future__ import annotations

import unittest

from backend.app.backfill_calendar_2025_2026 import CALENDAR_URL, build_calendar_document
from backend.app.models import QueryPlan, UserProfile
from backend.app.search.engine import SearchEngine
from backend.app.storage import DocumentStore


class CalendarBackfillTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.store = DocumentStore()
        cls.store.init_db()
        cls.store.upsert_documents([build_calendar_document()])

    def test_calendar_document_uses_official_calendar_entry_url(self) -> None:
        with self.store.connect() as conn:
            row = conn.execute("SELECT title, url, publish_date, body FROM documents WHERE url = ?", (CALENDAR_URL,)).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "东南大学2025-2026学年校历")
        self.assertEqual(row["publish_date"], "2025-04-24")
        self.assertIn("2025-2026学年寒假：2026年1月26日至2026年3月1日", row["body"])
        self.assertIn("2025-2026学年暑假：2026年7月6日至2026年8月23日", row["body"])

    def test_calendar_queries_recall_calendar_first(self) -> None:
        engine = SearchEngine(self.store)
        queries = [
            "2025-2026学年校历原文",
            "今年什么时候放寒假",
            "2026年暑假什么时候开始",
        ]
        for query in queries:
            with self.subTest(query=query):
                plan = QueryPlan(
                    intent="answer_question",
                    normalized_query=query,
                    retrieval_keywords=[query, "校历", "寒假", "暑假"],
                    expanded_queries=["东南大学2025-2026学年校历"],
                    time_scope="current",
                )
                hits = engine.search(plan, UserProfile(student_type="本科生"), limit=3)

                self.assertTrue(hits)
                self.assertEqual(hits[0].title, "东南大学2025-2026学年校历")
                self.assertEqual(hits[0].url, CALENDAR_URL)


if __name__ == "__main__":
    unittest.main()
