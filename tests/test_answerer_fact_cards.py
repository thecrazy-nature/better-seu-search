from __future__ import annotations

import unittest

from backend.app.ai.answerer import Answerer
from backend.app.models import QueryPlan, SearchHit


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

    def test_fact_cards_do_not_require_exact_quote_match(self) -> None:
        answerer = Answerer()
        hit = SearchHit(
            id=3,
            title="关于测试流程的通知",
            url="https://jwc.seu.edu.cn/2026/0603/test.htm",
            source="教务处",
            publish_date="2026-06-03",
            snippet="学生通过系统提交申请。",
            score=5,
            chunk_kind="body",
        )
        source = {
            "ref": "[1]",
            "title": hit.title,
            "source": hit.source,
            "publish_date": hit.publish_date,
            "url": hit.url,
            "snippet": hit.snippet,
            "chunk_kind": "body",
            "_hit": hit,
            "_support_text": "学生通过系统提交申请。",
        }
        payload = {
            "facts": [
                {
                    "slot": "process",
                    "claim": "学生需要通过系统提交申请。",
                    "source_ref": "[1]",
                    "quote": "通过线上平台提交申请",
                    "is_direct": True,
                    "confidence": 0.72,
                    "evidence_type": "body",
                    "reason": "AI 对原文做了近义改写，也应保留以保证总结可生成。",
                }
            ]
        }

        facts, rejected = answerer._validate_fact_cards(payload, [source])

        self.assertEqual(rejected, 0)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["claim"], "学生需要通过系统提交申请。")

    def test_reader_final_answer_keeps_markdown_layout(self) -> None:
        answerer = Answerer()
        hit = SearchHit(
            id=4,
            title="关于测试报名的通知",
            url="https://jwc.seu.edu.cn/2026/0604/test.htm",
            source="教务处",
            publish_date="2026-06-04",
            snippet="报名时间为6月4日至6月8日。",
            score=5,
        )
        source = {
            "ref": "[1]",
            "title": hit.title,
            "source": hit.source,
            "publish_date": hit.publish_date,
            "url": hit.url,
            "_hit": hit,
        }
        facts = [
            {
                "slot": "time",
                "claim": "报名时间为6月4日至6月8日。",
                "source_ref": "[1]",
                "quote": "报名时间为6月4日至6月8日",
                "is_direct": True,
                "confidence": 0.9,
                "evidence_type": "body",
                "reason": "原文直接说明时间。",
                "_source": source,
            }
        ]
        payload = {
            "final_answer": "**结论：报名时间为6月4日至6月8日。**\n\n关键时间：\n- 报名时间为6月4日至6月8日。"
        }

        answer = answerer._answer_from_reader_payload(payload, facts, [source])

        self.assertIsNotNone(answer)
        self.assertIn("\n\n关键时间：\n- 报名时间", answer or "")
        self.assertIn("参考信息源：", answer or "")

    def test_reader_tasks_follow_requested_slots(self) -> None:
        answerer = Answerer()
        plan = QueryPlan(
            intent="eligibility_query",
            normalized_query="转专业",
            entities={"requested_slots": ["time", "audience", "condition", "material"]},
        )

        tasks = answerer._reader_tasks(plan)

        self.assertEqual(tasks, ["deadline", "eligibility", "material"])

    def test_document_and_attachment_queries_skip_ai_readers(self) -> None:
        answerer = Answerer()

        self.assertEqual(
            answerer._reader_tasks(QueryPlan(intent="find_document", normalized_query="校历")),
            [],
        )
        self.assertEqual(
            answerer._reader_tasks(QueryPlan(intent="attachment_query", normalized_query="申请表")),
            [],
        )

    def test_parallel_reader_results_are_merged_without_composer_ai(self) -> None:
        answerer = Answerer()
        hit = SearchHit(
            id=5,
            title="关于测试申请的通知",
            url="https://jwc.seu.edu.cn/2026/0605/test.htm",
            source="教务处",
            publish_date="2026-06-05",
            snippet="申请时间为6月5日至6月8日。",
            score=5,
        )
        source = {
            "ref": "[1]",
            "title": hit.title,
            "source": hit.source,
            "publish_date": hit.publish_date,
            "url": hit.url,
            "_hit": hit,
        }

        payload = answerer._combine_reader_results(
            [
                {
                    "task": "deadline",
                    "answer_section": "**结论：申请时间为6月5日至6月8日 [1]。**",
                    "confidence": "high",
                    "facts": [
                        {
                            "slot": "time",
                            "claim": "申请时间为6月5日至6月8日。",
                            "source_ref": "[1]",
                            "quote": "申请时间为6月5日至6月8日",
                            "is_direct": True,
                            "confidence": 0.9,
                            "evidence_type": "body",
                        }
                    ],
                },
                {
                    "task": "material",
                    "answer_section": "材料与附件：需要下载申请表 [1]。",
                    "confidence": "medium",
                    "facts": [
                        {
                            "slot": "material",
                            "claim": "需要下载申请表。",
                            "source_ref": "[1]",
                            "quote": "申请表",
                            "is_direct": True,
                            "confidence": 0.75,
                            "evidence_type": "attachment_list",
                        }
                    ],
                },
            ],
            [source],
        )

        self.assertIn("final_answer", payload)
        self.assertIn("时间信息", payload["final_answer"])
        self.assertIn("材料与附件", payload["final_answer"])
        self.assertEqual(payload["confidence"], "medium")
        self.assertEqual(len(payload["facts"]), 2)


if __name__ == "__main__":
    unittest.main()
