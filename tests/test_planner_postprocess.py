from __future__ import annotations

import unittest

from backend.app.ai.planner import QueryPlanner
from backend.app.models import QueryPlan, UserProfile


class PlannerPostprocessTest(unittest.TestCase):
    def test_ai_plan_time_question_is_not_left_as_generic_answer(self) -> None:
        planner = QueryPlanner()
        plan = QueryPlan(
            intent="answer_question",
            confidence=0.8,
            normalized_query="四六级 报名",
            sub_questions=["四六级报名时间"],
            retrieval_keywords=["四六级", "报名"],
            entities={"topic": "四六级", "action": "报名"},
            need_answer_summary=True,
        )

        processed = planner._apply_safety_normalization(plan, "四六级报名时间", UserProfile(student_type="本科生"))

        self.assertEqual(processed.intent, "deadline_query")
        self.assertTrue(processed.need_answer_summary)
        self.assertIn("time", processed.entities["requested_slots"])


if __name__ == "__main__":
    unittest.main()
