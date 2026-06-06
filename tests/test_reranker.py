from __future__ import annotations

import unittest

from backend.app.ai.reranker import AIReranker, RerankItem, RerankResult
from backend.app.models import SearchHit


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


if __name__ == "__main__":
    unittest.main()
