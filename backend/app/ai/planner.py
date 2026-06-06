from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from ..config import settings
from ..models import QueryPlan, UserProfile
from ..search.synonyms import SYNONYMS, expand_terms
from .client import AIUnavailableError, make_ai_client
from .presets import OUTPUT_PRESETS, output_preset_for_intent
from .prompts import QUERY_PLANNER_SYSTEM


COLLEGE_RE = re.compile(r"([\u4e00-\u9fff]{2,20}(?:学院|书院|系))")
GRADE_RE = re.compile(r"(20\d{2}\s*级|大[一二三四五六]|研[一二三]|博士[一二三四五])")
STUDENT_TYPE_RE = re.compile(r"(本科生|研究生|硕士|博士|留学生|交换生)")
ACTION_KEYWORDS = ["报名", "报考", "申请", "下载", "查询", "办理", "考试", "缴费", "核对", "打印", "提交", "截止", "公示"]
PROFILE_STAGE_RE = re.compile(r"(20\d{2}\s*级|大[一二三四五六](?:上|下|上学期|下学期)?|研[一二三](?:上|下)?|博士[一二三四五](?:上|下)?|本科生|研究生)")
PROFILE_NEED_RE = re.compile(r"(重要|事项|安排|有什么事|有什么事情|该看什么|要做什么|需要关注|关注什么|别错过|提醒)")
ATTACHMENT_RE = re.compile(r"(申请表|附件|下载|名单|pdf|PDF|word|Word|excel|Excel|表格|安排表)")
MATERIAL_RE = re.compile(r"(材料|证明|原件|复印件|申请表|附件|名单|表格|PDF|pdf|Word|word|Excel|excel)")
PROCESS_RE = re.compile(r"(怎么办|怎么申请|怎么打印|流程|步骤|材料|去哪里办|去哪办|如何|在哪|入口|系统在哪|办理|怎么报|咋报)")
ELIGIBILITY_RE = re.compile(r"(能不能|可不可以|可以.*吗|是否|符合|资格|条件|要求|大[一二三四].*能|研究生.*能|本科生.*能)")
ENTRY_RE = re.compile(r"(入口|系统|平台|网址|链接|在哪|在哪里|哪里|登录|信息门户|办事大厅|下载专区)")
EXCEPTION_RE = re.compile(r"(特殊|突发|紧急|例外|逾期|补办|报备|告知|无效|不得|不能|不予|取消|未提前|未及时)")
COMPARISON_RE = re.compile(r"(对比|不同|区别|差异|有什么不同|和.*比)")
DOCUMENT_LOOKUP_RE = re.compile(r"(原文|链接|网址|URL|url|PDF|pdf|附件|下载|通知链接|文件链接|帮我找|找一下|找一找|在哪里下载)")
CONTENT_QUESTION_RE = re.compile(r"(有什么要求|什么要求|有哪些要求|有什么不同|什么时候|啥时候|时间|能不能|可不可以|怎么|如何|流程|材料|入口|系统|截止|条件|资格|总结)")
AVAILABILITY_RE = re.compile(r"(现在|当前|还能|还可以|还来得及|截止了吗|截止没|能不能.*申请|能不能.*报名)")
LATEST_RE = re.compile(r"(最近|最新|今天|本周|这周|新发)")
OFF_TOPIC_RE = re.compile(r"(吃什么|吃啥|外卖|天气|电影|游戏|股票|彩票|闲聊|笑话)")
OTHER_SCHOOL_RE = re.compile(r"(南京大学|清华大学|北京大学|复旦大学|上海交通大学|浙江大学|中国科学技术大学|哈尔滨工业大学)")
SCHOOL_SEARCH_RE = re.compile(
    r"(东南大学|教务处|研究生院|学院|书院|本科生|研究生|转专业|保研|推免|四六级|CET|报名|申请|"
    r"成绩|补考|缓考|重修|毕业|学分|课程|选课|考试|校历|寒假|暑假|通知|公告|附件|表格|名单|"
    r"流程|条件|要求|材料|入口|链接|下载)"
)
NEGATIVE_RE = re.compile(r"(?:不要|别|排除|过滤|不看|不要给我|不是|非)([\u4e00-\u9fffA-Za-z0-9、，,]{2,24}?)(?:通知|公告|信息|结果|$)")
YEAR_RE = re.compile(r"(20\d{2})")
CALENDAR_FOCUS_RE = re.compile(r"(寒假|暑假|放假|开学|报到|上课|考试周|教学周)")
TIME_QUESTION_RE = re.compile(r"(什么时候|啥时候|何时|几号|哪天|时间|截止|DDL|ddl)")
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")
COLLEGE_ALIASES = {
    "计算机学院": "计算机科学与工程学院",
    "计软智学院": "计算机科学与工程学院",
}


class QueryPlanner:
    def __init__(self) -> None:
        self.client = make_ai_client()

    def plan(self, user_query: str, profile: UserProfile | None = None) -> QueryPlan:
        profile = profile or UserProfile()
        if not self.client:
            raise AIUnavailableError("AI Planner 不可用：未配置 API Key 或 AI 客户端初始化失败。")
        if not self._ai_planner_enabled(user_query):
            if self._looks_off_topic(user_query.strip()):
                return self._unknown_plan(user_query)
            raise AIUnavailableError("AI Planner 已被配置关闭，无法理解用户问题。")
        ai_plan = self._plan_with_ai(user_query, profile)
        if ai_plan:
            return ai_plan
        raise AIUnavailableError("AI Planner 调用失败或返回内容不可解析。")

    def _plan_with_ai(self, user_query: str, profile: UserProfile) -> QueryPlan | None:
        try:
            response = self.client.chat.completions.create(
                model=settings.ai_model,
                temperature=0,
                messages=[
                    {"role": "system", "content": QUERY_PLANNER_SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "user_query": user_query,
                                "profile": profile.model_dump(exclude_none=True),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            data = json.loads(content)
            return self._normalize_ai_plan_data(data, user_query, profile)
        except Exception:
            return None

    @staticmethod
    def _unknown_plan(user_query: str) -> QueryPlan:
        return QueryPlan(
            intent="unknown",
            confidence=1.0,
            normalized_query=user_query.strip() or user_query,
            sub_questions=[],
            retrieval_keywords=[],
            expanded_queries=[],
            entities={"requested_slots": []},
            filters={},
            need_answer_summary=False,
            output_preset=output_preset_for_intent("unknown"),
            notes="AI Planner 未调用：问题不属于学校官网公开信息检索范围。",
        )

    def _normalize_ai_plan_data(self, data: dict[str, Any], user_query: str, profile: UserProfile) -> QueryPlan | None:
        data = dict(data or {})
        data["normalized_query"] = str(data.get("normalized_query") or user_query).strip() or user_query
        data["sub_questions"] = self._as_list(data.get("sub_questions"))
        data["retrieval_keywords"] = self._as_list(data.get("retrieval_keywords"))
        data["expanded_queries"] = self._as_list(data.get("expanded_queries")) or expand_terms(data["normalized_query"])
        if not isinstance(data.get("entities"), dict):
            data["entities"] = {}
        if not isinstance(data.get("filters"), dict):
            data["filters"] = {}
        requested_slots = self._as_list(data["entities"].get("requested_slots"))
        if not requested_slots:
            requested_slots = self._extract_requested_slots(user_query, str(data.get("intent") or "answer_question"))
        data["entities"]["requested_slots"] = requested_slots
        for key in ("evidence_targets", "official_terms", "table_terms", "likely_sources"):
            data["entities"][key] = self._as_list(data["entities"].get(key))
        data["exclude_terms"] = self._as_list(data.get("exclude_terms")) or self._extract_exclude_terms(user_query)
        data["time_scope"] = data.get("time_scope") or self._extract_time_scope(user_query)
        data["authority_preference"] = data.get("authority_preference") or self._extract_authority_preference(user_query)
        for key in ("college", "grade", "student_type"):
            value = getattr(profile, key, None)
            if value and not data["filters"].get(key):
                data["filters"][key] = value
        try:
            plan = QueryPlan(**data)
        except ValidationError:
            return None
        if not plan.expanded_queries:
            plan.expanded_queries = expand_terms(plan.normalized_query)
        if not plan.sub_questions:
            plan.sub_questions = [plan.normalized_query]
        plan.retrieval_keywords = self._merge_terms(
            plan.retrieval_keywords,
            self._minimal_retrieval_keywords(plan, profile),
        )[:32]
        if not plan.output_preset or plan.output_preset not in OUTPUT_PRESETS:
            plan.output_preset = output_preset_for_intent(plan.intent)
        return self._postprocess_ai_plan(plan, user_query, profile)

    def _postprocess_ai_plan(self, plan: QueryPlan, user_query: str, profile: UserProfile) -> QueryPlan:
        return self._apply_safety_normalization(plan, user_query, profile)

    def _apply_safety_normalization(self, plan: QueryPlan, user_query: str, profile: UserProfile) -> QueryPlan:
        """Keep AI understanding intact while filling safety-critical fields."""
        text = user_query.strip()
        surface_intent = self._safety_intent_override(text, plan.intent)
        if surface_intent != plan.intent:
            plan.intent = surface_intent
        elif plan.intent == "unknown" and self._looks_like_school_search(text):
            if self._looks_like_latest_query(text):
                plan.intent = "latest_updates"
            elif ELIGIBILITY_RE.search(text):
                plan.intent = "eligibility_query"
            elif PROCESS_RE.search(text):
                plan.intent = "process_guide"
            elif TIME_QUESTION_RE.search(text) or AVAILABILITY_RE.search(text):
                plan.intent = "deadline_query"
            elif self._looks_like_document_lookup(text):
                plan.intent = "find_document"
            else:
                plan.intent = "answer_question"
        should_summarize = bool(CONTENT_QUESTION_RE.search(text) or AVAILABILITY_RE.search(text))
        if plan.intent == "unknown":
            plan.need_answer_summary = False
        elif plan.intent == "latest_updates":
            plan.need_answer_summary = False
        elif should_summarize:
            plan.need_answer_summary = True
        elif plan.intent in {"find_document", "attachment_query"}:
            plan.need_answer_summary = False
        elif plan.intent in {"deadline_query", "process_guide", "eligibility_query", "profile_query", "answer_question"}:
            plan.need_answer_summary = True
        plan.output_preset = output_preset_for_intent(plan.intent)

        text_college = self._normalize_college(self._first_match(COLLEGE_RE, text))
        profile_college = self._normalize_college(profile.college)
        preferred_college = text_college or profile_college
        if preferred_college:
            if not plan.entities.get("college"):
                plan.entities["college"] = preferred_college
            if not plan.filters.get("college"):
                plan.filters["college"] = preferred_college
        for key in ("grade", "student_type"):
            value = getattr(profile, key, None)
            if value and not plan.filters.get(key):
                plan.filters[key] = value

        plan.exclude_terms = self._merge_terms(plan.exclude_terms, self._extract_exclude_terms(text))
        plan.time_scope = plan.time_scope or self._extract_time_scope(text)
        plan.authority_preference = self._normalize_college(
            plan.authority_preference or self._extract_authority_preference(text)
        )
        plan.entities["requested_slots"] = self._merge_terms(
            self._as_list(plan.entities.get("requested_slots")),
            self._extract_requested_slots(text, plan.intent),
        )
        if not plan.sub_questions:
            plan.sub_questions = [plan.normalized_query]
        plan.retrieval_keywords = self._merge_terms(
            plan.retrieval_keywords,
            self._minimal_retrieval_keywords(plan, profile),
        )[:32]
        if not plan.output_preset or plan.output_preset not in OUTPUT_PRESETS:
            plan.output_preset = output_preset_for_intent(plan.intent)
        return plan

    @staticmethod
    def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
        match = pattern.search(text)
        if not match:
            return None
        return match.group(1).replace(" ", "")

    @staticmethod
    def _normalize_query_text(text: str) -> str:
        cleaned = re.sub(r"(帮我|请|一下|查一下|找一下|我想|想知道|有没有|官网|链接|原文)", "", text)
        cleaned = re.sub(r"(不要|别|排除|过滤|不看|不要给我|不是|非)[\u4e00-\u9fffA-Za-z0-9、，,]{2,24}", "", cleaned)
        cleaned = re.sub(r"(什么时候|啥时候|何时|几号|哪天|截止了吗|截止没|在哪看|在哪里看|在哪里|在哪|怎么|如何)", "", cleaned)
        cleaned = cleaned.replace("报？", "报名").replace("报?", "报名")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ?？。,.，")
        return cleaned or text

    @staticmethod
    def _extract_topic(text: str) -> str | None:
        exact_topics = [
            "寒假工作安排",
            "暑期工作安排",
            "暑假工作安排",
            "毕业审核",
            "毕业资格",
            "毕业生学分核对",
            "成绩复核",
            "成绩单",
            "补考安排表",
            "补考安排",
            "转专业申请表",
            "准考证",
            "推免",
            "保研",
            "微专业",
        ]
        for topic in exact_topics:
            if topic in text:
                return topic
        extra_topics = {
            "校历": ["寒假", "暑假", "放假", "开学"],
            "毕业审核": ["毕业审核", "毕业资格", "毕业预审核", "毕业资格审查", "学分核对"],
            "毕业生学分核对": ["毕业生学分核对", "毕业班", "选课学分核对"],
            "补考": ["补考安排", "补考安排表"],
            "评教": ["评教系统", "评教入口", "期末评教"],
            "成绩": ["成绩复核", "成绩单", "成绩打印"],
            "重修": ["挂科", "不及格重修", "及格重修", "课程重修"],
            "辅修": ["微专业"],
        }
        for topic, aliases in extra_topics.items():
            if topic in text or any(alias in text for alias in aliases):
                return topic
        for topic, aliases in SYNONYMS.items():
            if topic in text or any(alias in text for alias in aliases):
                return topic
        return None

    @staticmethod
    def _extract_action(text: str) -> str | None:
        for keyword in ACTION_KEYWORDS:
            if keyword in text:
                return "报名" if keyword == "报考" else keyword
        if "报名" not in text and re.search(r"(怎么|咋|啥时候|什么时候).{0,8}报", text):
            return "报名"
        return None

    @staticmethod
    def _extract_calendar_focus(text: str) -> str | None:
        match = CALENDAR_FOCUS_RE.search(text)
        return match.group(1) if match else None

    @staticmethod
    def _extract_requested_slots(text: str, intent: str) -> list[str]:
        slots: list[str] = []
        if TIME_QUESTION_RE.search(text) or intent == "deadline_query":
            slots.append("time")
        if AVAILABILITY_RE.search(text):
            slots.append("time")
        if PROCESS_RE.search(text) or intent == "process_guide":
            slots.append("process")
        if MATERIAL_RE.search(text) or intent == "attachment_query":
            slots.append("material")
        if ENTRY_RE.search(text):
            slots.append("entry")
        if ELIGIBILITY_RE.search(text) or intent == "eligibility_query":
            slots.extend(["audience", "condition"])
        if EXCEPTION_RE.search(text):
            slots.append("exception")
        if COMPARISON_RE.search(text):
            slots.append("comparison")
        if intent == "find_document":
            slots.append("source")
        if intent == "profile_query":
            slots.extend(["audience", "time", "condition"])
        if intent == "answer_question" and not slots:
            slots.extend(["condition", "time", "process", "material"])
        return QueryPlanner._merge_terms([], slots)

    @staticmethod
    def _as_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if item]
        if isinstance(value, str) and value:
            return [value]
        return []

    @staticmethod
    def _extract_exclude_terms(text: str) -> list[str]:
        terms: list[str] = []
        for match in NEGATIVE_RE.finditer(text):
            raw = match.group(1)
            raw = re.sub(r"(给我|相关|的|这个|这类)$", "", raw).strip(" ，,、")
            for part in re.split(r"[、，,\s]+", raw):
                part = part.strip()
                if len(part) >= 2 and part not in {"通知", "公告", "信息", "结果"}:
                    terms.append(part)
        if "不要给我项目报名" in text or "不要项目报名" in text:
            terms.extend(["项目报名", "项目", "交流项目", "暑期项目", "科研项目"])
        if "不要教务处" in text or "别给我教务处" in text:
            terms.append("教务处")
        if "不要学校官网" in text or "别给我学校官网" in text:
            terms.append("学校官网")
        return QueryPlanner._merge_terms([], terms)

    @staticmethod
    def _extract_time_scope(text: str) -> str | None:
        short_year = re.search(r"(\d{2})\s*届", text)
        if short_year:
            return "20" + short_year.group(1)
        if re.search(r"(今年|本年度|本学年|当前|现在|最新|最近)", text):
            return "current"
        for year in YEAR_RE.finditer(text):
            if text[year.end() : year.end() + 1] == "级":
                continue
            return year.group(1)
        if re.search(r"(近两年|两年内|最近两年)", text):
            return "recent_2y"
        if re.search(r"(历史|往年|历年|以前)", text):
            return "historical"
        return None

    @staticmethod
    def _extract_authority_preference(text: str) -> str | None:
        if "不要教务处" in text or "别给我教务处" in text:
            if re.search(r"(学校官网|学校通知|学校新闻|新闻|全校)", text):
                return "学校官网"
            return None
        if "不要学校官网" in text or "别给我学校官网" in text:
            return None
        if "教务处" in text:
            return "教务处"
        if "研究生院" in text:
            return "研究生院"
        college = QueryPlanner._first_match(COLLEGE_RE, text)
        if college:
            return QueryPlanner._normalize_college(college)
        if re.search(r"(学校官网|学校通知|全校)", text):
            return "学校官网"
        return None

    @staticmethod
    def _normalize_college(value: str | None) -> str | None:
        if not value:
            return None
        compact = value.replace(" ", "")
        if "和" in compact:
            compact = compact.split("和")[-1]
        return COLLEGE_ALIASES.get(compact, compact)

    @staticmethod
    def _profile_query_terms(plan: QueryPlan, profile: UserProfile) -> list[str]:
        terms: list[str] = []
        for key in ("college", "grade", "student_type"):
            value = plan.filters.get(key) or plan.entities.get(key) or getattr(profile, key, None)
            if isinstance(value, str) and value:
                terms.append(value)
        if any("计算机" in term for term in terms):
            terms.extend(["计算机学院", "计软智学院", "计算机科学与工程学院"])
        if "大二" in terms:
            terms.extend(["2024级", "二年级"])
        return QueryPlanner._merge_terms([], terms)

    @staticmethod
    def _build_sub_questions(text: str, plan: QueryPlan) -> list[str]:
        topic = plan.entities.get("topic")
        action = plan.entities.get("action")
        subject = " ".join(str(item) for item in [topic, action] if item) or plan.normalized_query or text
        slots = QueryPlanner._as_list(plan.entities.get("requested_slots"))
        labels = {
            "time": "时间/截止",
            "process": "流程/入口",
            "material": "材料/附件",
            "entry": "系统/链接",
            "audience": "适用对象",
            "condition": "条件/要求",
            "exception": "例外/限制",
            "comparison": "差异对比",
            "source": "原文来源",
        }
        questions = [f"{subject}：{labels.get(slot, slot)}" for slot in slots if slot]
        if not questions:
            questions.append(subject)
        return QueryPlanner._merge_terms([], questions)

    @staticmethod
    def _build_retrieval_keywords(text: str, plan: QueryPlan, profile: UserProfile) -> list[str]:
        values: list[str] = []
        values.append(plan.normalized_query)
        values.extend(plan.expanded_queries[:10])
        topic = plan.entities.get("topic")
        action = plan.entities.get("action")
        focus = plan.entities.get("calendar_focus")
        for value in (topic, action, focus, plan.authority_preference):
            if isinstance(value, str):
                values.append(value)
        for key in ("college", "grade", "student_type"):
            value = plan.filters.get(key) or plan.entities.get(key) or getattr(profile, key, None)
            if isinstance(value, str):
                values.append(value)
        values.extend(TOKEN_RE.findall(text or ""))
        return QueryPlanner._merge_terms([], values)[:24]

    @staticmethod
    def _minimal_retrieval_keywords(plan: QueryPlan, profile: UserProfile) -> list[str]:
        values: list[str] = [plan.normalized_query, *plan.expanded_queries[:8]]
        for value in plan.entities.values():
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, list):
                values.extend(str(item) for item in value if item)
        for key in ("college", "grade", "student_type"):
            value = plan.filters.get(key) or getattr(profile, key, None)
            if isinstance(value, str):
                values.append(value)
        values.extend(QueryPlanner._domain_query_expansions(plan.normalized_query))
        for value in plan.sub_questions:
            values.extend(QueryPlanner._domain_query_expansions(value))
        values.extend(TOKEN_RE.findall(plan.normalized_query or ""))
        return QueryPlanner._merge_terms([], values)[:24]

    @staticmethod
    def _domain_query_expansions(text: str) -> list[str]:
        text = text or ""
        expansions: list[str] = []
        if re.search(r"(毕业审核|毕业资格|毕业审查|毕业.*链接)", text):
            expansions.extend(["毕业班", "选课学分核对", "毕业资格审查", "培养方案总学分", "毕业生学分"])
        if re.search(r"(校历|寒假|暑假|放假|开学|报到)", text):
            expansions.extend(["校历", "教学日历", "学年校历", "寒假", "暑假", "开学", "报到", "教学周"])
        if re.search(r"(研究生.*成绩单|成绩单.*研究生)", text):
            expansions.extend(["研究生", "研究生院", "成绩单", "打印", "培养", "学籍"])
        if re.search(r"(转专业|接收方案|接收条件)", text):
            expansions.extend(["转专业", "接收条件", "信息一览表", "附件", "专业", "考核方式", "面试", "成绩要求"])
        if "计算机" in text:
            expansions.extend(["计算机科学与工程学院", "计算机学院", "计算机科学与技术", "计算机专业基础"])
        return expansions

    @staticmethod
    def _merge_terms(left: list[str], right: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for term in [*left, *right]:
            term = term.strip()
            if term and term not in seen:
                seen.add(term)
                merged.append(term)
        return merged

    @staticmethod
    def _looks_like_profile_stage_query(text: str) -> bool:
        return bool(PROFILE_STAGE_RE.search(text) and PROFILE_NEED_RE.search(text))

    @staticmethod
    def _safety_intent_override(text: str, current_intent: str = "answer_question") -> str:
        """Correct only broad output-mode mistakes; retrieval should rely on keywords, not intent."""
        if QueryPlanner._looks_off_topic(text):
            return "unknown"
        if QueryPlanner._looks_like_latest_query(text):
            if re.search(r"(学院|20\d{2}\s*级|大[一二三四]|本科生|研究生)", text):
                return "profile_query"
            return "latest_updates"
        if COMPARISON_RE.search(text):
            return "answer_question"
        if QueryPlanner._looks_like_document_lookup(text) and not CONTENT_QUESTION_RE.search(text):
            return "find_document"
        if QueryPlanner._looks_like_profile_stage_query(text):
            return "profile_query"
        return current_intent

    @staticmethod
    def _looks_like_time_query(text: str) -> bool:
        return bool(TIME_QUESTION_RE.search(text) or AVAILABILITY_RE.search(text))

    @staticmethod
    def _looks_like_latest_query(text: str) -> bool:
        return bool(LATEST_RE.search(text))

    @staticmethod
    def _looks_like_attachment_lookup(text: str) -> bool:
        return bool(ATTACHMENT_RE.search(text) and DOCUMENT_LOOKUP_RE.search(text) and not CONTENT_QUESTION_RE.search(text))

    @staticmethod
    def _looks_like_document_lookup(text: str) -> bool:
        return bool(
            DOCUMENT_LOOKUP_RE.search(text)
            or (re.search(r"(通知|公告|文件)", text) and not CONTENT_QUESTION_RE.search(text))
        )

    @staticmethod
    def _looks_off_topic(text: str) -> bool:
        if OFF_TOPIC_RE.search(text):
            return True
        if OTHER_SCHOOL_RE.search(text) and "东南大学" not in text:
            return True
        return False

    @staticmethod
    def _looks_like_school_search(text: str) -> bool:
        return bool(SCHOOL_SEARCH_RE.search(text)) and not QueryPlanner._looks_off_topic(text)

    @staticmethod
    def _should_override_normalized_query(current: str, topic: str) -> bool:
        if not current:
            return True
        return topic not in current and (current in topic or len(topic) >= len(current) + 2)

    @staticmethod
    def _ai_planner_enabled(text: str) -> bool:
        mode = settings.ai_planner_mode
        if mode in {"off", "false", "0", "disabled"}:
            return False
        text = text.strip()
        return bool(text) and not QueryPlanner._looks_off_topic(text)
