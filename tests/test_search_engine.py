from __future__ import annotations

import unittest

from backend.app.models import QueryPlan, UserProfile
from backend.app.search.engine import SearchEngine


class SearchEngineRecallTest(unittest.TestCase):
    def test_vector_recall_is_regular_channel_for_answer_queries(self) -> None:
        self.assertTrue(SearchEngine._should_use_vector_recall(QueryPlan(intent="answer_question", normalized_query="重修")))
        self.assertTrue(SearchEngine._should_use_vector_recall(QueryPlan(intent="find_document", normalized_query="毕业审核")))
        self.assertFalse(SearchEngine._should_use_vector_recall(QueryPlan(intent="latest_updates", normalized_query="最新通知")))
        self.assertFalse(SearchEngine._should_use_vector_recall(QueryPlan(intent="unknown", normalized_query="天气")))

    def test_metadata_only_topic_does_not_beat_direct_title_match(self) -> None:
        engine = SearchEngine()
        plan = QueryPlan(
            intent="find_document",
            normalized_query="毕业审核通知",
            retrieval_keywords=["毕业审核", "学分核对"],
            entities={"topic": "毕业审核"},
        )
        direct = {
            "id": 1,
            "title": "关于2026届毕业班同学选课学分核对的通知",
            "source": "教务处",
            "category": "",
            "snippet": "请毕业班同学进行选课学分核对。",
            "matched_chunk_text": "请毕业班同学进行选课学分核对。",
            "keywords": [],
            "topics": [],
            "chunk_tags": [],
            "attachments": [],
            "applicable_colleges": [],
            "applicable_grades": [],
            "student_types": [],
            "publish_date": "2026-04-10",
            "deadline": None,
        }
        metadata_only = {
            **direct,
            "id": 2,
            "title": "东南大学本科生如何办理辅修证书",
            "snippet": "证书办理流程说明。",
            "matched_chunk_text": "证书办理流程说明。",
            "keywords": ["毕业审核", "毕业资格审核"],
            "topics": ["毕业审核"],
            "chunk_tags": ["毕业审核"],
            "publish_date": "2019-01-03",
        }

        direct_score = engine._score(direct, plan, UserProfile(), base_score=1.0)
        metadata_score = engine._score(metadata_only, plan, UserProfile(), base_score=1.0)

        self.assertGreater(direct_score, metadata_score)

    def test_find_document_prefers_title_match_over_body_mention(self) -> None:
        engine = SearchEngine()
        plan = QueryPlan(
            intent="find_document",
            normalized_query="毕业审核通知链接",
            retrieval_keywords=["毕业审核", "毕业资格", "学分核对"],
            entities={"topic": "毕业审核"},
        )
        title_match = {
            "id": 1,
            "title": "关于2026届毕业班同学选课学分核对的通知",
            "source": "教务处",
            "category": "",
            "snippet": "毕业班同学选课学分核对。",
            "matched_chunk_text": "毕业班同学选课学分核对。",
            "keywords": [],
            "topics": [],
            "chunk_tags": [],
            "attachments": [],
            "applicable_colleges": [],
            "applicable_grades": [],
            "student_types": [],
            "publish_date": "2026-04-10",
            "deadline": None,
        }
        body_only = {
            **title_match,
            "id": 2,
            "title": "东南大学本科生如何办理辅修证书",
            "snippet": "通过毕业资格审核后可以办理相关证书。",
            "matched_chunk_text": "通过毕业资格审核后可以办理相关证书。",
            "keywords": ["毕业审核"],
            "topics": ["毕业审核"],
            "publish_date": "2019-01-03",
        }

        title_score = engine._score(title_match, plan, UserProfile(), base_score=1.0)
        body_score = engine._score(body_only, plan, UserProfile(), base_score=1.0)

        self.assertGreater(title_score, body_score)

    def test_multi_term_coverage_beats_single_broad_table_match(self) -> None:
        engine = SearchEngine()
        plan = QueryPlan(
            intent="eligibility_query",
            normalized_query="计算机学院转专业接收条件 考核方式",
            retrieval_keywords=["计算机学院", "转专业", "接收条件", "考核方式"],
            expanded_queries=["计算机科学与工程学院", "本科生转专业"],
            entities={"topic": "转专业", "action": "接收条件"},
            filters={"college": "计算机学院"},
            time_scope="current",
        )
        transfer_row = {
            "id": 1,
            "title": "关于做好2025-2026学年本科生转专业工作的通知",
            "source": "教务处",
            "category": "",
            "snippet": "计算机科学与工程学院 接收学生转专业信息一览表 接收条件 考核方式",
            "matched_chunk_text": "计算机科学与工程学院；转专业；接收条件：无不及格课程；考核方式：笔试、面试",
            "keywords": [],
            "topics": [],
            "chunk_tags": ["附件表格行"],
            "attachments": [],
            "applicable_colleges": [],
            "applicable_grades": [],
            "student_types": [],
            "publish_date": "2026-06-02",
            "deadline": None,
            "chunk_kind": "attachment_table_row",
        }
        broad_row = {
            **transfer_row,
            "id": 2,
            "title": "2026年微课比赛校内报名作品公示名单",
            "snippet": "信息科学与工程学院 计算机相关课程 2026年微课比赛名单",
            "matched_chunk_text": "信息科学与工程学院；大模型课程；计算机相关微课作品名单",
        }

        transfer_score = engine._score(transfer_row, plan, UserProfile(college="计算机学院"), base_score=1.0)
        broad_score = engine._score(broad_row, plan, UserProfile(college="计算机学院"), base_score=1.0)

        self.assertGreater(transfer_score, broad_score)

    def test_contact_table_does_not_beat_conditions_when_contact_not_requested(self) -> None:
        engine = SearchEngine()
        plan = QueryPlan(
            intent="answer_question",
            normalized_query="计算机学院转专业接收条件和考核方式",
            retrieval_keywords=["计算机学院", "转专业", "接收条件", "考核方式"],
            entities={"topic": "转专业", "action": "接收条件"},
            filters={"college": "计算机学院"},
        )
        condition_hit = {
            "id": 1,
            "title": "关于做好2025-2026学年本科生转专业工作的通知",
            "source": "教务处",
            "category": "",
            "snippet": "计算机科学与工程学院 转专业 接收条件 考核方式",
            "matched_chunk_text": "计算机科学与工程学院；接收条件；考核方式；成绩要求",
            "keywords": [],
            "topics": [],
            "chunk_tags": [],
            "attachments": [],
            "applicable_colleges": [],
            "applicable_grades": [],
            "student_types": [],
            "publish_date": "2026-01-08",
            "deadline": None,
        }
        contact_hit = {
            **condition_hit,
            "id": 2,
            "attachment_name": "各学院负责转专业工作教务老师及联系电话一览表.pdf",
            "snippet": "计算机科学与工程学院 教务老师 联系电话",
            "matched_chunk_text": "学院 姓名 联系电话 计算机科学与工程学院 张老师 5209XXXX",
        }

        condition_score = engine._score(condition_hit, plan, UserProfile(), base_score=1.0)
        contact_score = engine._score(contact_hit, plan, UserProfile(), base_score=1.0)

        self.assertGreater(condition_score, contact_score)


if __name__ == "__main__":
    unittest.main()
