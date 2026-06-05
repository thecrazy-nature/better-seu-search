from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import settings
from ..models import QueryPlan, SearchHit, UserProfile
from ..storage import DocumentStore
from .client import make_ai_client


EvidenceLabel = Literal["direct_answer", "supporting", "weak", "wrong_topic", "stale"]
JudgeStatus = Literal["used", "skipped", "failed", "fallback_no_accept"]
JUDGE_CANDIDATE_LIMIT = 12
JUDGE_BODY_CHARS = 1000
JUDGE_RELATED_CHARS = 1800
JUDGE_BODY_WINDOW_CHARS = 460


EVIDENCE_JUDGE_SYSTEM = """
你是东南大学官网检索系统的 AI Evidence Judge（证据裁判）。
你的任务是阅读候选“文章证据包”，判断每篇文章是否真的能回答用户问题；你不写最终答案。

你只能使用输入中的 user_query、current_date、query_plan、profile 和 candidates。
不要补充候选之外的事实，不要生成 URL，不要改写 publish_date。
candidate.ai_metadata 只是离线读文后的检索提示，不能当作直接证据；直接证据必须来自 title、publish_date、body_excerpt、matched_chunk、related_chunks 或 attachments。

标签含义：
- direct_answer：候选能直接回答用户问题，或就是用户要找的原文/通知/附件本身。
- supporting：候选能回答用户问题中的一个关键槽位，但单独不足以完整回答。
- weak：只有字面词或泛主题相关，不能回答用户真正问的点。
- wrong_topic：候选主题和用户问题不是同一事项。
- stale：用户问最新、最近、当前、现在还能不能、是否截止等当前性问题时，候选明显过旧，且不是长期有效制度文件。

判断步骤：
1. 先从 query_plan.entities.requested_slots 和 user_query 判断用户要的槽位：source、time、process、material、entry、audience、condition、exception、comparison、answer。
2. 对每个 candidate 先判断“是不是同一事项”，再判断证据包中是否有能回答这些槽位的直接证据。
3. 如果 matched_chunk 是附件、名单、表格，只能作为这篇文章的一个证据点；不要因为附件里偶然出现关键词就认定整篇文章相关。
4. 用户给出精确业务名时，必须匹配同一事项；同属一个大类但业务不同要标 weak 或 wrong_topic。比如只出现“毕业设计/论文/名单”不能自动等同于“毕业审核/毕业资格/学分核对”。
5. 用户问原文/链接/通知/附件时，候选标题、附件名或来源事项必须对得上；正文里偶然出现一个泛词不够。
6. 用户问时间时，要区分 publish_date 和正文里的申请/报名/截止/考试/活动时间。
7. 用户问现在还能不能时，只保留能说明申请、报名、办理窗口或截止规则的候选；后续考试、审核、活动时间不能替代申请截止判断。
8. 用户问最近、最新、当前时，publish_date 是判断新旧的主依据；正文活动时间不能冒充文章发布时间。
9. 本地检索负责召回，你负责过滤噪声；候选不能回答就标 weak/wrong_topic/stale，不要为了凑结果保留。
10. 输出中的 id 必须来自 candidates；不要输出候选中不存在的 id。

只输出 JSON：
{
  "judgments": [
    {
      "id": 123,
      "label": "direct_answer",
      "confidence": 0.92,
      "reason": "一句话说明为什么能或不能回答",
      "answerable_slots": ["source", "time", "process"],
      "keep": true
    }
  ],
  "notes": "一句话总结整体证据质量"
}
"""


class EvidenceJudgment(BaseModel):
    id: int
    label: EvidenceLabel
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str = ""
    answerable_slots: list[str] = Field(default_factory=list)
    keep: bool | None = None


class EvidenceJudgeResult(BaseModel):
    judgments: list[EvidenceJudgment] = Field(default_factory=list)
    notes: str | None = None


class EvidenceJudgeReport(BaseModel):
    status: JudgeStatus
    notes: str | None = None
    candidate_count: int = 0
    judged_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    accepted: list[dict[str, Any]] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AIEvidenceJudge:
    """Judge candidate evidence before answer composition.

    Local retrieval should recall generously. This class is the AI reading step
    that decides which recalled items are actually usable evidence.
    """

    KEEP_LABELS = {"direct_answer", "supporting"}
    LABEL_PRIORITY = {
        "direct_answer": 4,
        "supporting": 3,
        "weak": 1,
        "stale": 0,
        "wrong_topic": -1,
    }

    def __init__(self) -> None:
        self.client = make_ai_client()
        self.store = DocumentStore()

    def judge(
        self,
        user_query: str,
        plan: QueryPlan,
        hits: list[SearchHit],
        profile: UserProfile | None = None,
        limit: int = 8,
    ) -> tuple[list[SearchHit], EvidenceJudgeReport]:
        candidate_count = min(len(hits), JUDGE_CANDIDATE_LIMIT)
        if not hits:
            return hits, EvidenceJudgeReport(status="skipped", notes="没有候选来源。", candidate_count=0)
        if not self.client or len(hits) < 2:
            reason = "AI 客户端不可用。" if not self.client else "候选来源少于 2 条。"
            return hits[:limit], EvidenceJudgeReport(status="skipped", notes=reason, candidate_count=candidate_count)
        if not self._should_judge(user_query, plan, hits):
            return hits[:limit], EvidenceJudgeReport(
                status="skipped",
                notes="当前配置跳过 AI 证据裁判。",
                candidate_count=candidate_count,
            )

        result = self._judge_with_ai(user_query, plan, hits, profile)
        if not result or not result.judgments:
            return hits[:limit], EvidenceJudgeReport(
                status="failed",
                notes="AI 证据裁判未返回可用判断，已退回本地检索排序。",
                candidate_count=candidate_count,
                warnings=["evidence_judge_failed_open"],
            )

        judged_hits, report = self._apply_judgments(hits, result, limit)
        return judged_hits, report

    @staticmethod
    def _should_judge(user_query: str, plan: QueryPlan, hits: list[SearchHit]) -> bool:
        mode = settings.ai_evidence_judge_mode
        if mode in {"off", "false", "0", "disabled"}:
            return False
        if mode in {"always", "on", "true", "1"}:
            return True
        if plan.intent == "unknown":
            return False
        if any(term in user_query for term in ("对比", "不同", "区别")):
            return True
        return True

    def _judge_with_ai(
        self,
        user_query: str,
        plan: QueryPlan,
        hits: list[SearchHit],
        profile: UserProfile | None,
    ) -> EvidenceJudgeResult | None:
        payload = [self._candidate_payload(hit, plan) for hit in hits[:JUDGE_CANDIDATE_LIMIT]]
        try:
            response = self.client.chat.completions.create(
                model=settings.ai_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": EVIDENCE_JUDGE_SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "user_query": user_query,
                                "current_date": date.today().isoformat(),
                                "query_plan": plan.model_dump(mode="json"),
                                "profile": profile.model_dump(exclude_none=True) if profile else {},
                                "candidates": payload,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            )
            content = response.choices[0].message.content or "{}"
            return EvidenceJudgeResult(**json.loads(content))
        except Exception:
            return None

    def _apply_judgments(
        self,
        hits: list[SearchHit],
        result: EvidenceJudgeResult,
        limit: int,
    ) -> tuple[list[SearchHit], EvidenceJudgeReport]:
        by_id = {hit.id: hit for hit in hits}
        used: set[int] = set()
        accepted: list[tuple[EvidenceJudgment, SearchHit]] = []
        rejected: list[tuple[EvidenceJudgment, SearchHit]] = []

        for judgment in result.judgments:
            hit = by_id.get(judgment.id)
            if not hit or judgment.id in used:
                continue
            used.add(judgment.id)
            should_keep = judgment.keep if judgment.keep is not None else judgment.label in self.KEEP_LABELS
            if judgment.label not in self.KEEP_LABELS or not should_keep:
                rejected.append((judgment, hit))
                continue
            accepted.append((judgment, self._annotate_hit(hit, judgment)))

        accepted.sort(
            key=lambda item: (
                self.LABEL_PRIORITY.get(item[0].label, 0),
                item[0].confidence,
                item[1].score,
            ),
            reverse=True,
        )
        judged_hits = [hit for _, hit in accepted[:limit]]
        accepted_items = [self._report_item(judgment, hit, kept=True) for judgment, hit in accepted]
        rejected_items = [self._report_item(judgment, hit, kept=False) for judgment, hit in rejected]
        if not judged_hits:
            notes = result.notes or "AI 证据裁判认为候选来源不足以直接回答该问题。"
            report = EvidenceJudgeReport(
                status="fallback_no_accept",
                notes=notes,
                candidate_count=min(len(hits), JUDGE_CANDIDATE_LIMIT),
                judged_count=len(used),
                accepted_count=0,
                rejected_count=len(rejected_items),
                accepted=[],
                rejected=rejected_items,
                warnings=["evidence_judge_no_accepted_candidates"],
            )
            return hits[:limit], report
        report = EvidenceJudgeReport(
            status="used",
            notes=result.notes,
            candidate_count=min(len(hits), JUDGE_CANDIDATE_LIMIT),
            judged_count=len(used),
            accepted_count=len(accepted_items),
            rejected_count=len(rejected_items),
            accepted=accepted_items[:limit],
            rejected=rejected_items,
        )
        return judged_hits, report

    @staticmethod
    def _annotate_hit(hit: SearchHit, judgment: EvidenceJudgment) -> SearchHit:
        reason = _clean(judgment.reason, 140)
        slots = "、".join(_clean(slot, 20) for slot in judgment.answerable_slots[:5] if slot)
        prefix = f"AI证据判断：{judgment.label}"
        if slots:
            prefix = f"{prefix}；可回答：{slots}"
        if reason:
            prefix = f"{prefix}；原因：{reason}"
        old_note = hit.relevance_note or ""
        relevance_note = f"{prefix}；{old_note}" if old_note else prefix
        return hit.model_copy(
            update={
                "relevance_note": relevance_note,
                "evidence_judge_label": judgment.label,
                "evidence_judge_confidence": round(float(judgment.confidence), 3),
                "evidence_judge_reason": reason,
                "evidence_judge_answerable_slots": [
                    _clean(str(slot), 24) for slot in judgment.answerable_slots[:8] if str(slot).strip()
                ],
            }
        )

    @staticmethod
    def _report_item(judgment: EvidenceJudgment, hit: SearchHit, kept: bool) -> dict[str, Any]:
        return {
            "id": hit.id,
            "title": hit.title,
            "url": hit.url,
            "source": hit.source,
            "publish_date": hit.publish_date,
            "label": judgment.label,
            "confidence": round(float(judgment.confidence), 3),
            "keep": kept,
            "answerable_slots": [_clean(str(slot), 24) for slot in judgment.answerable_slots[:8] if str(slot).strip()],
            "reason": _clean(judgment.reason, 180),
            "local_score": round(float(hit.score), 3),
            "chunk_kind": hit.chunk_kind,
            "attachment_name": hit.attachment_name,
        }

    def _candidate_payload(self, hit: SearchHit, plan: QueryPlan) -> dict:
        context = hit.matched_chunk_text or hit.snippet
        row = self.store.get_document(hit.id)
        body_excerpt = ""
        ai_metadata: dict[str, Any] = {}
        terms = self._context_terms(plan, hit)
        if row:
            body_text = _strip_embedded_attachment_text(row["body"] or "")
            body_excerpt = _focused_text_windows(body_text, terms, JUDGE_BODY_CHARS)
            if not body_excerpt:
                body_excerpt = _clean(body_text, JUDGE_BODY_CHARS)
            ai_metadata = _json_dict(row["ai_metadata_json"] if "ai_metadata_json" in row.keys() else None)
        related_chunks = self._related_chunk_payload(hit.id, terms)
        return {
            "id": hit.id,
            "title": hit.title,
            "url": hit.url,
            "source": hit.source,
            "category": hit.category,
            "publish_date": hit.publish_date,
            "snippet": _clean(hit.snippet, 520),
            "matched_chunk": {
                "heading": hit.heading,
                "page": hit.page,
                "attachment_name": hit.attachment_name,
                "chunk_kind": hit.chunk_kind,
                "text": _clean(context, 1200),
            },
            "body_excerpt": body_excerpt,
            "related_chunks": related_chunks,
            "attachments": [
                {"name": item.get("name"), "url": item.get("url")}
                for item in hit.attachments[:8]
                if item.get("name") or item.get("url")
            ],
            "applicable_colleges": hit.applicable_colleges,
            "applicable_grades": hit.applicable_grades,
            "student_types": hit.student_types,
            "topics": hit.topics,
            "keywords": hit.keywords,
            "ai_metadata": _public_ai_metadata(ai_metadata),
            "chunk_tags": hit.chunk_tags,
            "deadline": hit.deadline,
            "score": hit.score,
            "local_relevance_note": hit.relevance_note,
        }

    def _related_chunk_payload(self, doc_id: int, terms: list[str]) -> list[dict[str, Any]]:
        try:
            with self.store.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT heading, page, attachment_name, chunk_kind, chunk_text
                    FROM document_chunks
                    WHERE document_id = ?
                    ORDER BY id
                    LIMIT 120
                    """,
                    (doc_id,),
                ).fetchall()
        except Exception:
            return []

        scored: list[tuple[int, int, int, dict[str, Any]]] = []
        for position, row in enumerate(rows):
            text = str(row["chunk_text"] or "")
            if not text.strip():
                continue
            haystack = "\n".join(
                [
                    str(row["heading"] or ""),
                    str(row["attachment_name"] or ""),
                    str(row["chunk_kind"] or ""),
                    text,
                ]
            )
            term_score = sum(2 for term in terms if term and term in haystack)
            score = term_score
            chunk_kind = str(row["chunk_kind"] or "")
            if chunk_kind == "title":
                score += 1
            if chunk_kind == "attachment_list" and term_score:
                score += 1
            if chunk_kind == "attachment_text" and term_score:
                score += 1
            if score <= 0:
                continue
            type_priority = 3
            if chunk_kind == "title":
                type_priority = 5
            elif chunk_kind == "body":
                type_priority = 4
            elif chunk_kind == "attachment_list":
                type_priority = 3
            elif chunk_kind == "attachment_text":
                type_priority = 1
            scored.append(
                (
                    score,
                    type_priority,
                    -position,
                    {
                        "heading": row["heading"],
                        "page": row["page"],
                        "attachment_name": row["attachment_name"],
                        "chunk_kind": row["chunk_kind"],
                        "text": _clean(text, 700),
                    },
                )
            )
        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)

        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        total = 0
        attachment_text_count = 0
        for _, _, _, item in scored:
            if item.get("chunk_kind") == "attachment_text":
                if attachment_text_count >= 2:
                    continue
                attachment_text_count += 1
            key = re.sub(r"\s+", "", item["text"][:120])
            if key in seen:
                continue
            seen.add(key)
            total += len(item["text"])
            if selected and total > JUDGE_RELATED_CHARS:
                break
            selected.append(item)
            if len(selected) >= 6:
                break
        return selected

    @staticmethod
    def _context_terms(plan: QueryPlan, hit: SearchHit) -> list[str]:
        values: list[str] = [
            plan.normalized_query,
            *plan.sub_questions,
            *plan.retrieval_keywords,
            *plan.expanded_queries,
            *hit.keywords[:8],
            *hit.topics,
        ]
        for value in plan.entities.values():
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, list):
                values.extend(str(item) for item in value if item)
        for value in plan.filters.values():
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, list):
                values.extend(str(item) for item in value if item)
        values.extend(_slot_context_terms(plan))
        terms: list[str] = []
        seen: set[str] = set()
        for value in values:
            for term in re.findall(r"[A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", str(value or "")):
                if term not in seen:
                    seen.add(term)
                    terms.append(term)
            value_text = str(value or "").strip()
            if len(value_text) >= 2 and value_text not in seen:
                seen.add(value_text)
                terms.append(value_text)
        return terms[:36]


def _clean(text: str | None, max_chars: int) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _focused_text_windows(text: str | None, terms: list[str], max_chars: int) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    if not text.strip():
        return ""
    terms = _usable_terms(terms)
    if not terms:
        return ""

    windows: list[tuple[int, int, str]] = []
    for term in terms[:24]:
        for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            start = max(0, match.start() - JUDGE_BODY_WINDOW_CHARS // 2)
            end = min(len(text), match.end() + JUDGE_BODY_WINDOW_CHARS)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            if not snippet:
                continue
            score = sum(1 for item in terms if item and item in snippet)
            windows.append((score, -start, snippet))
            break

    if not windows:
        return ""
    windows.sort(reverse=True)
    selected: list[str] = []
    seen: set[str] = set()
    total = 0
    for _, _, snippet in windows:
        key = re.sub(r"\s+", "", snippet[:120])
        if key in seen:
            continue
        seen.add(key)
        line = f"正文相关窗口：{snippet}"
        if total + len(line) > max_chars and selected:
            break
        selected.append(line)
        total += len(line)
        if len(selected) >= 3:
            break
    return "\n".join(selected)[:max_chars]


def _strip_embedded_attachment_text(body: str) -> str:
    text = body or ""
    match = re.search(r"\n\s*(?:附件正文摘录[:：]|附件《[^》]+》正文摘录[:：])", text)
    if match:
        return text[: match.start()].strip()
    return text.strip()


def _usable_terms(values: list[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = re.sub(r"\s+", "", str(value or "")).strip()
        if len(text) < 2 or text in seen:
            continue
        seen.add(text)
        terms.append(text)
    return terms


def _slot_context_terms(plan: QueryPlan) -> list[str]:
    raw_slots = plan.entities.get("requested_slots")
    slots = raw_slots if isinstance(raw_slots, list) else []
    aliases = {
        "answer": ["结论", "说明", "要求"],
        "time": ["时间", "日期", "截止", "报名", "申请", "办理"],
        "process": ["流程", "步骤", "办理", "申请"],
        "material": ["材料", "附件", "提交", "申请表"],
        "entry": ["入口", "平台", "系统", "网址", "地点", "联系方式"],
        "audience": ["对象", "范围", "学生", "年级", "学院"],
        "condition": ["条件", "要求", "资格", "限制"],
        "exception": ["例外", "限制", "不得", "不予"],
        "comparison": ["区别", "不同", "对比"],
        "source": ["原文", "通知", "链接", "附件"],
    }
    terms: list[str] = []
    for slot in slots:
        terms.extend(aliases.get(str(slot), []))
    return terms


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _public_ai_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "topics",
        "keywords",
        "business_actions",
        "audience",
        "answerable_questions",
        "official_terms",
        "attachment_summaries",
        "confidence",
    }
    public = {key: value for key, value in metadata.items() if key in allowed and value}
    if isinstance(public.get("attachment_summaries"), list):
        public["attachment_summaries"] = public["attachment_summaries"][:6]
    return public


def _top_result_is_clear(hits: list[SearchHit]) -> bool:
    if len(hits) < 2:
        return True
    top, second = hits[0], hits[1]
    if top.score - second.score >= 1.8:
        return True
    if (top.relevance_note or "").count("；") >= 2 and top.score - second.score >= 0.8:
        return True
    return False
