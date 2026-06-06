from __future__ import annotations

import unittest

from backend.app.ai.planner import QueryPlanner
from backend.app.models import QueryPlan, UserProfile


class PlannerIntentTest(unittest.TestCase):
    def test_time_question_does_not_fall_back_to_answer_question(self) -> None:
        planner = QueryPlanner()

        plan = planner._apply_safety_normalization(
            QueryPlan(
                intent="answer_question",
                normalized_query="四六级报名时间",
                entities={"requested_slots": []},
            ),
            "四六级报名时间",
            UserProfile(student_type="本科生"),
        )

        self.assertEqual(plan.intent, "deadline_query")
        self.assertTrue(plan.need_answer_summary)
        self.assertIn("time", plan.entities["requested_slots"])

    def test_profile_terms_do_not_override_eligibility_intent(self) -> None:
        planner = QueryPlanner()

        plan = planner._apply_safety_normalization(
            QueryPlan(
                intent="profile_query",
                normalized_query="转专业",
                entities={"requested_slots": []},
            ),
            "计算机学院大二能不能转专业？",
            UserProfile(student_type="本科生"),
        )

        self.assertEqual(plan.intent, "eligibility_query")
        self.assertIn("audience", plan.entities["requested_slots"])
        self.assertIn("condition", plan.entities["requested_slots"])

    def test_latest_college_notice_stays_profile_query(self) -> None:
        planner = QueryPlanner()

        plan = planner._apply_safety_normalization(
            QueryPlan(
                intent="answer_question",
                normalized_query="计算机学院教务通知",
                entities={"requested_slots": []},
            ),
            "计算机学院最近有什么教务通知",
            UserProfile(student_type="本科生"),
        )

        self.assertEqual(plan.intent, "profile_query")

    def test_notice_requirements_are_answer_intent_not_find_document(self) -> None:
        planner = QueryPlanner()

        plan = planner._apply_safety_normalization(
            QueryPlan(
                intent="find_document",
                normalized_query="计算机学院转专业通知",
                entities={"requested_slots": []},
            ),
            "计算机学院转专业通知有什么要求？",
            UserProfile(college="计算机科学与工程学院", student_type="本科生"),
        )

        self.assertEqual(plan.intent, "eligibility_query")
        self.assertTrue(plan.need_answer_summary)
        self.assertIn("condition", plan.entities["requested_slots"])

    def test_latest_jwc_notice_is_latest_updates(self) -> None:
        planner = QueryPlanner()

        plan = planner._apply_safety_normalization(
            QueryPlan(
                intent="profile_query",
                normalized_query="教务处通知",
                entities={"requested_slots": []},
            ),
            "最近教务处有什么通知",
            UserProfile(),
        )

        self.assertEqual(plan.intent, "latest_updates")
        self.assertFalse(plan.need_answer_summary)


if __name__ == "__main__":
    unittest.main()
