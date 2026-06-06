from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import settings
from ..models import QueryPlan, SearchHit, UserProfile
from .client import AIUnavailableError, make_ai_client


RerankLabel = Literal["strong", "partial", "weak", "wrong_topic", "stale"]
RerankStatus = Literal["used", "skipped", "failed"]
RERANK_CANDIDATE_LIMIT = 12


RERANKER_SYSTEM = """
你是东南大学官网检索系统的轻量 AI Reranker。
你的任务只是在本地召回的一批候选中重新排序，不要回答用户问题，不要补充任何候选之外的信息。

判断重点：
1. 用户问“原文/链接/通知/PDF/附件/下载”时，优先把标题、附件名、命中片段真正对应用户事项的候选排前。
2. 用户问“什么时候/能不能/怎么办/有什么要求/有什么不同”时，优先把能支持这些槽位的候选排前，而不是只命中“通知、报名、打印、附件”等泛词。
3. “毕业审核/学分核对”不能被“毕业设计、毕业竞赛、图像采集”替代。
4. “成绩单打印”不能被“3D 打印、项目申请中的成绩单材料”替代。
5. “校历/寒暑假/放假”不能被“寒假交流项目、国际交流项目日程”替代。
6. 用户指定来源或排除内容时，要把来源不符或被排除的候选降级。
7. 最近/当前类问题优先看 publish_date；正文里的活动时间不能当作发布时间。

输出 JSON：
{
  "ranked": [
    {
      "id": 123,
      "label": "strong|partial|weak|wrong_topic|stale",
      "score": 0.0,
      "reason": "一句话说明为什么这个候选应该排在这里",
      "answerable_slots": ["source", "time", "material"]
    }
  ],
  "notes": "一句话总结排序依据"
}
"""


class RerankItem(BaseModel):
    id: int
    label: RerankLabel = "weak"
    score: float = Field(default=0.0, ge=0, le=1)
    reason: str = ""
    answerable_slots: list[str] = Field(default_factory=list)


class RerankResult(BaseModel):
    ranked: list[RerankItem] = Field(default_factory=list)
    notes: str | None = None


class RerankerReport(BaseModel):
    status: RerankStatus
    notes: str | None = None
    candidate_count: int = 0
    ranked_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class AIReranker:
    """Small AI-assisted reranker for noisy local retrieval candidates."""

    LABEL_PRIORITY = {
        "strong": 4,
        "partial": 3,
        "weak": 2,
        "stale": 1,
        "wrong_topic": 0,
    }

    def __init__(self) -> None:
        self.client = make_ai_client()

    def rerank(
        self,
        user_query: str,
        plan: QueryPlan,
        hits: list[SearchHit],
        profile: UserProfile | None = None,
    ) -> tuple[list[SearchHit], RerankerReport]:
        candidate_count = min(len(hits), RERANK_CANDIDATE_LIMIT)
        if not hits:
            return hits, RerankerReport(status="skipped", notes="没有候选来源。", candidate_count=0)
        if not self.client:
            if self._should_rerank(plan):
                raise AIUnavailableError("AI Reranker 不可用：未配置 API Key 或 AI 客户端初始化失败。")
            return hits, RerankerReport(status="skipped", notes="当前查询跳过 AI 重排。", candidate_count=candidate_count)
        if len(hits) < 2 or not self._should_rerank(plan):
            return hits, RerankerReport(status="skipped", notes="当前查询跳过 AI 重排。", candidate_count=candidate_count)

        result = self._rerank_with_ai(user_query, plan, hits, profile)
        if not result or not result.ranked:
            raise AIUnavailableError("AI Reranker 调用失败或返回内容不可解析。")
        return self._apply_rankings(hits, result)

    @staticmethod
    def _should_rerank(plan: QueryPlan) -> bool:
        mode = settings.ai_reranker_mode
        if mode in {"off", "false", "0", "disabled"}:
            return False
        if plan.intent == "unknown":
            return False
        if mode in {"find_only", "source_only"}:
            return plan.intent in {"find_document", "attachment_query", "latest_updates", "profile_query"}
        if mode in {"answer_only", "summary_only"}:
            return plan.need_answer_summary
        return True

    def _rerank_with_ai(
        self,
        user_query: str,
        plan: QueryPlan,
        hits: list[SearchHit],
        profile: UserProfile | None,
    ) -> RerankResult | None:
        payload = [self._candidate_payload(hit) for hit in hits[:RERANK_CANDIDATE_LIMIT]]
        try:
            response = self.client.chat.completions.create(
                model=settings.ai_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": RERANKER_SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "user_query": user_query,
                                "query_plan": plan.model_dump(mode="json"),
                                "profile": profile.model_dump(exclude_none=True) if profile else {},
                                "candidates": payload,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            )
            return RerankResult(**json.loads(response.choices[0].message.content or "{}"))
        except Exception:
            return None

    def _apply_rankings(
        self,
        hits: list[SearchHit],
        result: RerankResult,
    ) -> tuple[list[SearchHit], RerankerReport]:
        by_id = {hit.id: hit for hit in hits}
        used: set[int] = set()
        ranked: list[tuple[int, float, float, int, SearchHit]] = []
        for order, item in enumerate(result.ranked):
            hit = by_id.get(item.id)
            if not hit or item.id in used:
                continue
            used.add(item.id)
            priority = self.LABEL_PRIORITY.get(item.label, 1)
            ranked.append(
                (
                    priority,
                    float(item.score),
                    float(hit.score),
                    -order,
                    self._annotate_hit(hit, item),
                )
            )
        ranked.sort(reverse=True)
        selected = [item[4] for item in ranked]
        selected_ids = {hit.id for hit in selected}
        for hit in hits:
            if hit.id not in selected_ids:
                selected.append(hit)
                selected_ids.add(hit.id)
        notes = result.notes or "已使用 AI Reranker 进行轻量重排。"
        return selected, RerankerReport(
            status="used",
            notes=notes,
            candidate_count=min(len(hits), RERANK_CANDIDATE_LIMIT),
            ranked_count=len(used),
            warnings=[],
        )

    @staticmethod
    def _candidate_payload(hit: SearchHit) -> dict[str, Any]:
        attachments = [
            {"name": _clean(item.get("name"), 120), "url": item.get("url")}
            for item in hit.attachments[:6]
            if item.get("name") or item.get("url")
        ]
        return {
            "id": hit.id,
            "title": hit.title,
            "source": hit.source,
            "category": hit.category,
            "publish_date": hit.publish_date,
            "url": hit.url,
            "snippet": _clean(hit.snippet, 300),
            "matched_chunk_text": _clean(hit.matched_chunk_text, 360),
            "attachment_name": hit.attachment_name,
            "chunk_kind": hit.chunk_kind,
            "relevance_note": _clean(hit.relevance_note, 220),
            "attachments": attachments,
            "topics": hit.topics[:8],
            "keywords": hit.keywords[:10],
            "applicable_colleges": hit.applicable_colleges[:6],
            "applicable_grades": hit.applicable_grades[:6],
            "student_types": hit.student_types[:6],
        }

    @staticmethod
    def _annotate_hit(hit: SearchHit, item: RerankItem) -> SearchHit:
        reason = _clean(item.reason, 120)
        slots = "、".join(_clean(slot, 20) for slot in item.answerable_slots[:5] if slot)
        prefix = f"AI重排：{item.label}，匹配度 {float(item.score):.2f}"
        if slots:
            prefix = f"{prefix}；可回答：{slots}"
        if reason:
            prefix = f"{prefix}；原因：{reason}"
        old_note = hit.relevance_note or ""
        return hit.model_copy(update={"relevance_note": f"{prefix}；{old_note}" if old_note else prefix})


def _clean(value: object, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"
