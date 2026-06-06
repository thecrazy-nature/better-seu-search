from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import settings
from ..models import QueryPlan, SearchHit, UserProfile
from .client import AIUnavailableError, make_ai_client


EvidenceLabel = Literal["direct_answer", "supporting", "weak", "wrong_topic", "stale"]
JudgeStatus = Literal["used", "skipped", "failed"]
JUDGE_CANDIDATE_LIMIT = 12


EVIDENCE_JUDGE_SYSTEM = """
你是东南大学官网检索系统的轻量证据审查器。你的任务不是回答用户问题，而是温和判断候选资料与问题的相关程度，帮助排序。

重要原则：
1. 本地检索已经负责召回候选资料；你只做软排序和标注，不要过度过滤。
2. 如果候选资料可能有用，即使信息不完整，也保留为 supporting 或 weak。
3. 只有根据输入内容判断相关程度；不要补充候选之外的事实，不要生成 URL，不要改写 publish_date。
4. 用户问“原文/链接/通知/附件”时，标题、附件名或片段看起来对应即可给较高标签，不要求正文完整回答所有细节。
5. 用户问具体办事事项时，优先保留能直接说明流程、时间、材料、对象、入口、条件的资料。
6. 如果只是泛泛相关，标 weak；weak 也只是低优先级，不代表一定删除。

标签含义：
- direct_answer：候选很可能能直接回答问题，或就是用户要找的原文、通知、附件。
- supporting：候选能提供部分有用信息，适合作为补充来源。
- weak：候选相关性较弱，或只命中了一些泛词，但仍可能给最终回答提供线索。
- wrong_topic/stale：兼容标签。如果你非常确定主题不对或明显过时，可以使用，但系统仍只会降低优先级，不会硬删除。

只输出 JSON：
{
  "judgments": [
    {
      "id": 123,
      "label": "direct_answer",
      "confidence": 0.86,
      "reason": "一句话说明为什么这样判断",
      "answerable_slots": ["source", "time"],
      "keep": true
    }
  ],
  "notes": "一句话总结候选质量"
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
    """Lightweight AI evidence judge.

    This is intentionally a soft reranker. It annotates and reorders candidates
    but does not hard-filter local retrieval results, so a bad AI judgment cannot
    starve the answer composer of useful context.
    """

    LABEL_PRIORITY = {
        "direct_answer": 3,
        "supporting": 2,
        "weak": 1,
        "stale": 0,
        "wrong_topic": 0,
    }

    def __init__(self) -> None:
        self.client = make_ai_client()

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
        if not self.client:
            if self._should_judge(plan):
                raise AIUnavailableError("AI Evidence Judge 不可用：未配置 API Key 或 AI 客户端初始化失败。")
            return hits[:limit], EvidenceJudgeReport(
                status="skipped",
                notes="当前配置跳过 AI 证据审查。",
                candidate_count=candidate_count,
            )
        if len(hits) < 2:
            return hits[:limit], EvidenceJudgeReport(status="skipped", notes="候选来源少于 2 条。", candidate_count=candidate_count)
        if not self._should_judge(plan):
            return hits[:limit], EvidenceJudgeReport(
                status="skipped",
                notes="当前配置跳过 AI 证据审查。",
                candidate_count=candidate_count,
            )

        result = self._judge_with_ai(user_query, plan, hits, profile)
        if not result or not result.judgments:
            raise AIUnavailableError("AI Evidence Judge 调用失败或返回内容不可解析。")

        return self._apply_judgments(hits, result, limit)

    @staticmethod
    def _should_judge(plan: QueryPlan) -> bool:
        mode = settings.ai_evidence_judge_mode
        if mode in {"off", "false", "0", "disabled"}:
            return False
        if plan.intent == "unknown":
            return False
        return True

    def _judge_with_ai(
        self,
        user_query: str,
        plan: QueryPlan,
        hits: list[SearchHit],
        profile: UserProfile | None,
    ) -> EvidenceJudgeResult | None:
        payload = [self._candidate_payload(hit) for hit in hits[:JUDGE_CANDIDATE_LIMIT]]
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
        ranked: list[tuple[int, float, float, int, SearchHit, EvidenceJudgment]] = []

        for order, judgment in enumerate(result.judgments):
            hit = by_id.get(judgment.id)
            if not hit or judgment.id in used:
                continue
            used.add(judgment.id)
            priority = self.LABEL_PRIORITY.get(judgment.label, 0)
            if judgment.keep is False:
                priority = max(0, priority - 1)
            ranked.append(
                (
                    priority,
                    float(judgment.confidence),
                    float(hit.score),
                    -order,
                    self._annotate_hit(hit, judgment),
                    judgment,
                )
            )

        ranked.sort(reverse=True)
        selected: list[SearchHit] = [item[4] for item in ranked]
        selected_ids = {hit.id for hit in selected}
        for hit in hits:
            if hit.id not in selected_ids:
                selected.append(hit)
                selected_ids.add(hit.id)
            if len(selected) >= limit:
                break
        selected = selected[:limit]

        accepted_items = [
            self._report_item(item[5], item[4], kept=True)
            for item in ranked
            if item[4].id in {hit.id for hit in selected}
        ]
        ranked_selected_ids = {item["id"] for item in accepted_items}
        rejected_items = [
            self._report_item(item[5], item[4], kept=False)
            for item in ranked
            if item[4].id not in ranked_selected_ids
        ]
        notes = result.notes or "已使用旧版轻量证据审查器进行软排序。"
        if result.notes:
            notes = f"旧版轻量证据审查器软排序：{result.notes}"
        return selected, EvidenceJudgeReport(
            status="used",
            notes=notes,
            candidate_count=min(len(hits), JUDGE_CANDIDATE_LIMIT),
            judged_count=len(used),
            accepted_count=len(accepted_items),
            rejected_count=len(rejected_items),
            accepted=accepted_items,
            rejected=rejected_items,
            warnings=["evidence_judge_soft_rerank_no_hard_filter"],
        )

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

    @staticmethod
    def _candidate_payload(hit: SearchHit) -> dict[str, Any]:
        return {
            "id": hit.id,
            "title": hit.title,
            "url": hit.url,
            "source": hit.source,
            "category": hit.category,
            "publish_date": hit.publish_date,
            "snippet": _clean(hit.snippet, 700),
            "matched_chunk": {
                "heading": hit.heading,
                "page": hit.page,
                "attachment_name": hit.attachment_name,
                "chunk_kind": hit.chunk_kind,
                "text": _clean(hit.matched_chunk_text or "", 900),
            },
            "attachments": [
                {"name": item.get("name"), "url": item.get("url")}
                for item in hit.attachments[:8]
                if item.get("name") or item.get("url")
            ],
            "topics": hit.topics,
            "keywords": hit.keywords,
            "deadline": hit.deadline,
            "score": hit.score,
            "local_relevance_note": hit.relevance_note,
        }


def _clean(text: str | None, max_chars: int) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]
