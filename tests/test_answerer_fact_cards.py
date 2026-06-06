from __future__ import annotations

import unittest

from backend.app.ai.answerer import Answerer
from backend.app.models import SearchHit


class AnswererFactCardTest(unittest.TestCase):
    def test_fact_cards_keep_confidence_and_evidence_type(self) -> None:
        answerer = Answerer()
        hit = SearchHit(
            id=1,
            title="关于测试报名的通知",
            url="https://jwc.seu.edu.cn/2026/0601/test.htm",
            source="教务处",
            publish_date="2026-06-01",
            snippet="报名时间为6月1日至6月5日。",
            score=5,
            chunk_kind="body",
        )
        source = {
            "ref": "[1]",
            "title": hit.title,
            "source": hit.source,
            "publish_date": hit.publish_date,
            "url": hit.url,
            "chunk_kind": "body",
            "_hit": hit,
            "_support_text": "标题：关于测试报名的通知\n报名时间为6月1日至6月5日。",
        }
        payload = {
            "facts": [
                {
                    "slot": "time",
                    "claim": "报名时间为6月1日至6月5日。",
                    "source_ref": "[1]",
                    "quote": "报名时间为6月1日至6月5日",
                    "is_direct": True,
                    "confidence": 0.93,
                    "evidence_type": "body",
                    "reason": "原文直接说明报名时间。",
                }
            ]
        }

        facts, rejected = answerer._validate_fact_cards(payload, [source])
        evidence = answerer._evidence_from_fact_cards(facts)

        self.assertEqual(rejected, 0)
        self.assertEqual(facts[0]["confidence"], 0.93)
        self.assertEqual(facts[0]["evidence_type"], "body")
        self.assertEqual(evidence[0].fact_confidence, 0.93)
        self.assertEqual(evidence[0].evidence_type, "body")
        self.assertIn("事实置信度 0.93", evidence[0].reason)

    def test_fact_evidence_type_is_verified_against_quote_location(self) -> None:
        answerer = Answerer()
        hit = SearchHit(
            id=2,
            title="关于测试材料的通知",
            url="https://jwc.seu.edu.cn/2026/0602/test.htm",
            source="教务处",
            publish_date="2026-06-02",
            snippet="请下载附件。",
            score=5,
            attachments=[{"name": "申请表.docx", "url": "https://jwc.seu.edu.cn/files/app.docx"}],
        )
        source = {
            "ref": "[1]",
            "title": hit.title,
            "source": hit.source,
            "publish_date": hit.publish_date,
            "url": hit.url,
            "attachments": hit.attachments,
            "_hit": hit,
            "_support_text": "申请表.docx https://jwc.seu.edu.cn/files/app.docx",
        }
        payload = {
            "facts": [
                {
                    "slot": "material",
                    "claim": "需要下载申请表。",
                    "source_ref": "[1]",
                    "quote": "申请表.docx",
                    "is_direct": True,
                    "confidence": 0.8,
                    "evidence_type": "body",
                    "reason": "附件名直接对应材料。",
                }
            ]
        }

        facts, rejected = answerer._validate_fact_cards(payload, [source])

        self.assertEqual(rejected, 0)
        self.assertEqual(facts[0]["evidence_type"], "attachment_list")


if __name__ == "__main__":
    unittest.main()
