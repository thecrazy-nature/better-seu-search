from __future__ import annotations

import unittest

from backend.app.ai.evidence_judge import AIEvidenceJudge, EvidenceJudgeResult, EvidenceJudgment
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


class EvidenceJudgeTest(unittest.TestCase):
    def test_apply_judgments_reports_accepted_and_rejected_candidates(self) -> None:
        judge = AIEvidenceJudge()
        hits = [
            _hit(1, "关于毕业审核工作的通知", 5.0),
            _hit(2, "关于毕业设计竞赛获奖名单的公示", 7.0),
            _hit(3, "关于毕业生图像采集的通知", 4.0),
        ]
        result = EvidenceJudgeResult(
            judgments=[
                EvidenceJudgment(
                    id=1,
                    label="direct_answer",
                    confidence=0.91,
                    reason="标题和正文事项均为毕业审核。",
                    answerable_slots=["source", "time"],
                ),
                EvidenceJudgment(
                    id=2,
                    label="wrong_topic",
                    confidence=0.88,
                    reason="只是毕业设计竞赛名单，不是毕业审核。",
                    answerable_slots=[],
                    keep=False,
                ),
                EvidenceJudgment(
                    id=3,
                    label="supporting",
                    confidence=0.7,
                    reason="只能补充毕业生相关办理事项。",
                    answerable_slots=["material"],
                ),
            ],
            notes="保留毕业审核及补充材料来源。",
        )

        judged_hits, report = judge._apply_judgments(hits, result, limit=5)

        self.assertEqual(report.status, "used")
        self.assertEqual(report.accepted_count, 2)
        self.assertEqual(report.rejected_count, 1)
        self.assertEqual(report.rejected[0]["title"], "关于毕业设计竞赛获奖名单的公示")
        self.assertEqual([hit.id for hit in judged_hits], [1, 3])
        self.assertEqual(judged_hits[0].evidence_judge_label, "direct_answer")
        self.assertIn("可回答：source、time", judged_hits[0].relevance_note or "")
        self.assertIn("标题和正文事项均为毕业审核", judged_hits[0].relevance_note or "")


if __name__ == "__main__":
    unittest.main()
