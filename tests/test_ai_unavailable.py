from __future__ import annotations

import unittest

from backend.app.ai.answerer import Answerer
from backend.app.ai.client import AIUnavailableError
from backend.app.ai.planner import QueryPlanner
from backend.app.models import QueryPlan, SearchHit


class AIUnavailableTest(unittest.TestCase):
    def test_planner_raises_when_ai_client_missing(self) -> None:
        planner = QueryPlanner()
        planner.client = None

        with self.assertRaises(AIUnavailableError):
            planner.plan("四六级报名时间")

    def test_answerer_does_not_use_extractive_fallback_when_ai_missing(self) -> None:
        answerer = Answerer()
        answerer.client = None
        hit = SearchHit(
            id=1,
            title="关于测试报名的通知",
            url="https://jwc.seu.edu.cn/2026/0601/test.htm",
            source="教务处",
            publish_date="2026-06-01",
            snippet="报名时间为6月1日至6月5日。",
            score=5,
        )
        plan = QueryPlan(intent="deadline_query", normalized_query="测试报名时间", need_answer_summary=True)

        result = answerer.answer("测试报名时间", plan, [hit])

        self.assertEqual(result.confidence, "none")
        self.assertIn("AI 摘要不可用", result.answer)
        self.assertEqual(result.sources, [])


if __name__ == "__main__":
    unittest.main()
