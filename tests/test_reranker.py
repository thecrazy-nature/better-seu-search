from __future__ import annotations

import unittest
from unittest.mock import Mock

from backend.app.config import settings
from backend.app.ai.reranker import AIReranker, RerankItem, RerankResult
from backend.app.models import QueryPlan, SearchHit


def _hit(doc_id: int, title: str, score: float) -> SearchHit:
    return SearchHit(
        id=doc_id,
        title=title,
        url=f"https://jwc.seu.edu.cn/2026/060{doc_id}/page.htm",
        source="教务处",
        publish_date="2026-06-01",
        snippet="测试片段",
        score=score,
    )


class RerankerTest(unittest.TestCase):
    def test_apply_rankings_moves_strong_match_first(self) -> None:
        reranker = AIReranker()
        hits = [
            _hit(1, "关于毕业设计竞赛获奖名单的公示", 9.0),
            _hit(2, "关于2026届毕业班同学选课学分核对的通知", 5.0),
            _hit(3, "关于毕业生电子图像采集的通知", 7.0),
        ]
        result = RerankResult(
            ranked=[
                RerankItem(
                    id=2,
                    label="strong",
                    score=0.95,
                    reason="标题直接对应毕业班学分核对通知。",
                    answerable_slots=["source"],
                ),
                RerankItem(id=3, label="weak", score=0.3, reason="毕业生相关但不是学分核对。"),
                RerankItem(id=1, label="wrong_topic", score=0.1, reason="毕业设计竞赛不是毕业审核。"),
            ],
            notes="优先毕业学分核对。",
        )

        ranked, report = reranker._apply_rankings(hits, result)

        self.assertEqual(report.status, "used")
        self.assertEqual(report.ranked_count, 3)
        self.assertEqual([hit.id for hit in ranked], [2, 3, 1])
        self.assertIn("AI重排：strong", ranked[0].relevance_note or "")
        self.assertIn("毕业班学分核对", ranked[0].relevance_note or "")

    def test_parse_json_fenced_payload(self) -> None:
        payload = """```json
        {"ranked":[{"id":2,"score":0.9,"label":"strong","reason":"直接匹配"}]}
        ```"""

        result = AIReranker._result_from_payload(AIReranker._parse_json_object(payload))

        self.assertIsNotNone(result)
        self.assertEqual(result.ranked[0].id, 2)  # type: ignore[union-attr]
        self.assertEqual(result.ranked[0].label, "strong")  # type: ignore[union-attr]

    def test_score_only_payload_is_accepted(self) -> None:
        result = AIReranker._result_from_payload(
            {"scores": [{"id": 1, "score": 0.91}, {"id": 2, "score": 0.2}]}
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.ranked[0].label, "strong")  # type: ignore[union-attr]
        self.assertEqual(result.ranked[1].label, "wrong_topic")  # type: ignore[union-attr]

    def test_rerank_failure_keeps_local_order(self) -> None:
        old_mode = settings.ai_reranker_mode
        settings.ai_reranker_mode = "auto"
        reranker = AIReranker()
        reranker.client = Mock()
        reranker._rerank_with_ai = Mock(return_value=None)  # type: ignore[method-assign]
        hits = [_hit(1, "A", 2.0), _hit(2, "B", 1.0)]
        try:
            ranked, report = reranker.rerank("测试", QueryPlan(normalized_query="测试"), hits)
        finally:
            settings.ai_reranker_mode = old_mode

        self.assertEqual([hit.id for hit in ranked], [1, 2])
        self.assertEqual(report.status, "failed")
        self.assertTrue(report.warnings)


if __name__ == "__main__":
    unittest.main()
