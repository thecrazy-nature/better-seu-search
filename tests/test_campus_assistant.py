from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.campus_assistant import CampusAssistantStore
from backend.app.campus_assistant import OverseasApplicationAssistant
from backend.app.campus_assistant import OverseasApplicationRequest
from backend.app.campus_assistant import ReimbursementAssistant
from backend.app.campus_assistant import ReimbursementRequest


class CampusAssistantTest(unittest.TestCase):
    def test_reimbursement_answer_uses_isolated_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CampusAssistantStore(Path(tmp) / "assistant.sqlite3")
            assistant = ReimbursementAssistant(store)

            response = assistant.answer(
                ReimbursementRequest(
                    question="我的项目xxx产生了一笔设备费用，如何报销？",
                    project_code="61234567",
                    expense_type="设备",
                    invoice_date="2026-10-12",
                    payment_target="单位",
                )
            )

            stats = store.stats()

        self.assertEqual(response.dataset, "campus_assistant")
        self.assertIn("网上预约报销", response.answer)
        self.assertIn("项目负责人", response.actors)
        self.assertTrue(any("预算书" in item for item in response.materials + response.warnings))
        self.assertTrue(any("6/7/8" in item for item in response.warnings))
        self.assertTrue(any("https://www.seu.edu.cn/103/" in item for item in response.systems))
        self.assertTrue(response.sources)
        self.assertTrue(all("seu.edu.cn" in source.url for source in response.sources))
        self.assertEqual(stats["dataset"], "campus_assistant")
        self.assertGreaterEqual(stats["sources"], 4)
        self.assertGreaterEqual(stats["services"], 2)

    def test_missing_fields_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assistant = ReimbursementAssistant(CampusAssistantStore(Path(tmp) / "assistant.sqlite3"))
            response = assistant.answer(ReimbursementRequest(question="我的项目xxx产生了一笔费用，如何报销？"))

        self.assertIn("项目名称", response.missing_fields)
        self.assertIn("项目号/经费号", response.missing_fields)
        self.assertIn("费用类型", response.missing_fields)

    def test_overseas_application_uses_own_service_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CampusAssistantStore(Path(tmp) / "assistant.sqlite3")
            assistant = OverseasApplicationAssistant(store)

            response = assistant.answer(
                OverseasApplicationRequest(
                    question="我是研究生，要去日本参加国际会议，如何申请？",
                    applicant_type="研究生",
                    destination="日本",
                    visit_type="国际会议",
                    funding_source="导师科研项目",
                    start_date="2026-09-01",
                )
            )

        self.assertEqual(response.dataset, "campus_assistant")
        self.assertIn("出国", response.answer)
        self.assertTrue(any("国际合作处" in item for item in response.actors))
        self.assertTrue(any("http://ehall.seu.edu.cn/" in item for item in response.systems))
        self.assertTrue(any("https://oic.seu.edu.cn/18918/list.htm" in item for item in response.systems))
        self.assertTrue(any("导师" in item or "申请表" in item for item in response.materials + response.steps))
        self.assertTrue(response.sources)
        self.assertTrue(all("seu.edu.cn" in source.url for source in response.sources))
        self.assertFalse(any("网上预约报销" in source.title for source in response.sources))


if __name__ == "__main__":
    unittest.main()
