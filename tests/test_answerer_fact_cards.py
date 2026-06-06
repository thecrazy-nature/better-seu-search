from __future__ import annotations

import unittest
from unittest.mock import Mock

from backend.app.ai.answerer import Answerer
from backend.app.backfill_calendar_2025_2026 import CALENDAR_URL, build_calendar_document
from backend.app.models import QueryPlan, SearchHit
from backend.app.storage import DocumentStore


class AnswererFactCardTest(unittest.TestCase):
    def test_simple_answer_composer_maps_used_sources(self) -> None:
        answerer = Answerer()
        hit = SearchHit(
            id=10,
            title="关于测试报名的通知",
            url="https://jwc.seu.edu.cn/2026/0610/test.htm",
            source="教务处",
            publish_date="2026-06-10",
            snippet="报名时间为6月10日至6月12日。",
            score=5,
            chunk_kind="body",
        )
        plan = QueryPlan(intent="deadline_query", normalized_query="测试报名时间", need_answer_summary=True)
        answerer._source_context_for_reader = Mock(return_value="报名时间为6月10日至6月12日。")  # type: ignore[method-assign]
        answerer.client = Mock()
        answerer.client.chat.completions.create.return_value = Mock(
            choices=[
                Mock(
                    message=Mock(
                        content=(
                            '{"final_answer":"**结论：报名时间为6月10日至6月12日 [1]。**\\n\\n参考信息源：\\n'
                            '[1] 来源：《关于测试报名的通知》，2026-06-10，https://jwc.seu.edu.cn/2026/0610/test.htm",'
                            '"confidence":"high","used_refs":["[1]"],"evidence_notes":["原文说明报名时间。"]}'
                        )
                    )
                )
            ]
        )

        result = answerer.answer("测试报名时间", plan, [hit])

        self.assertEqual(result.confidence, "high")
        self.assertEqual([source.id for source in result.sources], [10])
        self.assertIn("报名时间为6月10日至6月12日", result.answer)

    def test_simple_answer_composer_uses_ai_ranked_source_order(self) -> None:
        answerer = Answerer()
        broad_hit = SearchHit(
            id=11,
            title="关于测试活动报名的通知",
            url="https://jwc.seu.edu.cn/2026/0611/broad.htm",
            source="教务处",
            publish_date="2026-06-11",
            snippet="正文泛泛提到报名。",
            score=9,
            chunk_kind="body",
        )
        direct_hit = SearchHit(
            id=12,
            title="关于测试考试报名时间的通知",
            url="https://jwc.seu.edu.cn/2026/0612/direct.htm",
            source="教务处",
            publish_date="2026-06-12",
            snippet="考试报名时间为6月12日至6月14日。",
            score=7,
            chunk_kind="body",
        )
        plan = QueryPlan(intent="deadline_query", normalized_query="测试考试报名时间", need_answer_summary=True)
        answerer._source_context_for_reader = Mock(
            side_effect=lambda hit, *_args, **_kwargs: (
                "考试报名时间为6月12日至6月14日。" if hit.id == 12 else "正文泛泛提到报名。"
            )
        )  # type: ignore[method-assign]
        answerer.client = Mock()
        answerer.client.chat.completions.create.return_value = Mock(
            choices=[
                Mock(
                    message=Mock(
                        content=(
                            '{"final_answer":"**结论：考试报名时间为6月12日至6月14日 [2]。**",'
                            '"confidence":"high",'
                            '"ranked_refs":["[2]","[1]"],'
                            '"used_refs":["[2]"],'
                            '"source_reviews":['
                            '{"ref":"[2]","verdict":"direct","score":0.95,"reason":"直接说明考试报名时间。","answer_points":["报名时间"]},'
                            '{"ref":"[1]","verdict":"weak","score":0.2,"reason":"只泛泛提到报名。","answer_points":[]}'
                            '],'
                            '"evidence_notes":["[2] 直接说明报名时间。"]}'
                        )
                    )
                )
            ]
        )

        result = answerer.answer("测试考试报名时间", plan, [broad_hit, direct_hit])

        self.assertEqual([source.id for source in result.sources], [12, 11])
        self.assertIn("AI相关性判断 [2]（direct）", result.evidence_notes[0])

    def test_simple_answer_composer_accepts_plain_text_answer(self) -> None:
        answerer = Answerer()
        hit = SearchHit(
            id=13,
            title="关于测试转专业的通知",
            url="https://jwc.seu.edu.cn/2026/0613/transfer.htm",
            source="教务处",
            publish_date="2026-06-13",
            snippet="接收条件和考核方式见附件。",
            score=5,
            chunk_kind="body",
        )
        plan = QueryPlan(intent="answer_question", normalized_query="测试转专业条件", need_answer_summary=True)
        answerer._source_context_for_reader = Mock(return_value="接收条件和考核方式见附件。")  # type: ignore[method-assign]
        answerer.client = Mock()
        answerer.client.chat.completions.create.return_value = Mock(
            choices=[
                Mock(
                    message=Mock(
                        content="**结论：接收条件和考核方式见附件 [1]。**\n\n参考信息源：\n[1] 来源：《关于测试转专业的通知》，2026-06-13，https://jwc.seu.edu.cn/2026/0613/transfer.htm"
                    )
                )
            ]
        )

        result = answerer.answer("测试转专业条件", plan, [hit])

        self.assertIn("接收条件和考核方式见附件", result.answer)
        self.assertEqual([source.id for source in result.sources], [13])

    def test_simple_answer_composer_accepts_plain_text_uncertain_answer(self) -> None:
        answerer = Answerer()
        payload = answerer._payload_from_plain_answer("无法在候选来源中找到明确的计算机学院接收条件。相关来源见 [1]。")

        self.assertIsNotNone(payload)
        self.assertIn("无法在候选来源中找到明确", payload["final_answer"])

    def test_simple_answer_composer_extracts_final_answer_from_jsonish_text(self) -> None:
        answerer = Answerer()
        payload = answerer._payload_from_plain_answer(
            '{ "final_answer": "**结论：可以申请，条件见附件 [1]。**\\n\\n参考信息源：\\n[1] 来源：《通知》", '
            '"confidence": "high", "ranked_refs": ["[1]"]'
        )

        self.assertIsNotNone(payload)
        self.assertTrue(payload["final_answer"].startswith("**结论：可以申请"))
        self.assertIn("[1]", payload["used_refs"])

    def test_summary_failure_falls_back_to_ranked_sources(self) -> None:
        answerer = Answerer()
        hit = SearchHit(
            id=14,
            title="关于测试转专业接收条件的通知",
            url="https://jwc.seu.edu.cn/2026/0614/transfer.htm",
            source="教务处",
            publish_date="2026-06-14",
            snippet="接收条件见附件。",
            score=5,
            chunk_kind="attachment_text",
            attachment_name="接收条件一览表.pdf",
        )
        plan = QueryPlan(intent="answer_question", normalized_query="测试转专业条件", need_answer_summary=True)
        answerer._source_context_for_reader = Mock(return_value="接收条件见附件。")  # type: ignore[method-assign]
        answerer.client = Mock()
        answerer.client.chat.completions.create.side_effect = RuntimeError("timeout")

        result = answerer.answer("测试转专业条件", plan, [hit])

        self.assertIn("已找到相关官网资料", result.answer)
        self.assertIn("命中附件：接收条件一览表.pdf", result.answer)
        self.assertEqual(result.confidence, "low")
        self.assertEqual([source.id for source in result.sources], [14])

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

    def test_deadline_context_keeps_focused_calendar_window(self) -> None:
        store = DocumentStore()
        store.init_db()
        store.upsert_documents([build_calendar_document()])
        with store.connect() as conn:
            row = conn.execute("SELECT id FROM documents WHERE url = ?", (CALENDAR_URL,)).fetchone()
        answerer = Answerer()
        answerer.store = store
        hit = SearchHit(
            id=int(row["id"]),
            title="东南大学2025-2026学年校历",
            url=CALENDAR_URL,
            source="教务处",
            publish_date="2025-04-24",
            snippet="东南大学2025-2026学年校历",
            score=10,
            topics=["校历", "寒假"],
            keywords=["2025-2026学年校历", "寒假"],
        )
        plan = QueryPlan(
            intent="deadline_query",
            normalized_query="今年什么时候放寒假？",
            retrieval_keywords=["校历", "寒假"],
            expanded_queries=["东南大学2025-2026学年校历"],
            entities={"requested_slots": ["time"]},
        )

        context = answerer._source_context_for_reader(hit, plan, max_chars=1100)

        self.assertIn("2025-2026学年寒假：2026年1月26日至2026年3月1日", context)
        self.assertLessEqual(len(context), 1100)

    def test_reference_source_url_uses_hit_url_not_attachment_url(self) -> None:
        answerer = Answerer()
        hit = SearchHit(
            id=21,
            title="东南大学2025-2026学年校历",
            url="https://jwc.seu.edu.cn/dndx2025w2026xnxl/list.htm",
            source="教务处",
            publish_date="2025-04-24",
            snippet="寒假：2026年1月26日至3月1日。",
            score=10,
            attachments=[
                {
                    "name": "正式文章页",
                    "url": "https://jwc.seu.edu.cn/2025/0424/c44492a526066/page.htm",
                }
            ],
        )
        answer = (
            "**结论：寒假为2026年1月26日至3月1日 [1]。**\n\n"
            "参考信息源：\n"
            "[1] 来源：《东南大学2025-2026学年校历》，2025-04-24，"
            "https://jwc.seu.edu.cn/2025/0424/c44492a526066/page.htm"
        )

        normalized = answerer._normalize_reference_source_urls(answer, [hit])

        self.assertIn("https://jwc.seu.edu.cn/dndx2025w2026xnxl/list.htm", normalized)
        self.assertNotIn("https://jwc.seu.edu.cn/2025/0424/c44492a526066/page.htm", normalized)


if __name__ == "__main__":
    unittest.main()
