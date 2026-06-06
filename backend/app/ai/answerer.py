from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any

from ..config import settings
from ..models import AnswerResult, EvidenceItem, QueryPlan, SearchHit
from ..storage import DocumentStore
from .client import make_ai_client
from .prompts import FACT_EXTRACTOR_SYSTEM
from .prompts import LIGHT_READER_SYSTEMS
from .validator import validate_answer_against_hits


FACT_SOURCE_LIMIT = 8
FACT_PRIMARY_CONTEXT_CHARS = 1700
FACT_SECONDARY_CONTEXT_CHARS = 1100
FACT_BODY_WINDOW_CHARS = 360
FACT_MAX_CARDS = 14
MAX_PARALLEL_READERS = 3
FACT_ALLOWED_SLOTS = {
    "answer",
    "time",
    "process",
    "material",
    "entry",
    "audience",
    "condition",
    "exception",
    "comparison",
    "source",
    "other",
}
FACT_ALLOWED_EVIDENCE_TYPES = {
    "title",
    "publish_date",
    "body",
    "attachment",
    "attachment_list",
    "table",
    "mixed",
    "unknown",
}
FACT_EVIDENCE_TYPE_PRIORITY = {
    "body": 5,
    "publish_date": 5,
    "title": 4,
    "mixed": 3,
    "attachment": 2,
    "attachment_list": 2,
    "table": 1,
    "unknown": 0,
}
FACT_SLOT_LABELS = {
    "answer": "结论判断",
    "time": "关键时间",
    "process": "办理流程",
    "material": "材料/附件",
    "entry": "入口/地点/联系方式",
    "audience": "适用对象",
    "condition": "条件/要求",
    "exception": "例外/限制",
    "comparison": "对比说明",
    "source": "原文信息",
    "other": "其他依据",
}


class Answerer:
    def __init__(self) -> None:
        self.client = make_ai_client()
        self.store = DocumentStore()

    def answer(self, user_query: str, plan: QueryPlan, hits: list[SearchHit]) -> AnswerResult:
        evidence = self._build_evidence(plan, hits)
        warnings = self._build_warnings(plan, hits)
        if not hits:
            return self._finalize_answer(
                AnswerResult(
                    answer="未在已收录的学校官网公开信息中找到明确依据。可以换一种说法，或先扩大来源范围后重新检索。",
                    confidence="none",
                    sources=[],
                    evidence=[],
                    evidence_notes=[],
                    warnings=["没有可引用来源，AI 不会生成事实性结论。"],
                ),
                hits,
            )

        if not plan.need_answer_summary:
            return self._finalize_answer(self._document_list_answer(hits, evidence, warnings), hits)

        if not self._ai_enabled():
            return self._finalize_answer(
                self._ai_unavailable_answer(
                    hits,
                    warnings,
                    "AI 摘要不可用：未配置 API Key、AI 客户端初始化失败，或答案生成模块被配置关闭。",
                ),
                hits,
            )

        ai_answer = self._answer_with_ai_fact_cards(user_query, plan, hits, evidence, warnings)
        if ai_answer:
            return self._finalize_answer(ai_answer, hits)

        return self._finalize_answer(
            self._ai_unavailable_answer(
                hits,
                warnings,
                "AI 摘要不可用：Fact Reader 调用失败，或 AI 未能从候选资料中抽取出可用答案。",
            ),
            hits,
        )

    def _answer_with_ai_fact_cards(
        self,
        user_query: str,
        plan: QueryPlan,
        hits: list[SearchHit],
        evidence: list[EvidenceItem],
        warnings: list[str],
    ) -> AnswerResult | None:
        sources = self._source_payload_for_fact_extraction(plan, hits)
        if not sources:
            return None

        payload = self._read_facts_with_light_readers(user_query, plan, sources, warnings)
        if not payload:
            payload = self._read_facts_with_ai(user_query, plan, sources, evidence, warnings)
        if not payload:
            return None

        facts, rejected_count = self._normalize_fact_cards(payload, sources)
        if not facts:
            return None

        fact_warnings = self._fact_warnings(payload, rejected_count, warnings)
        answer = self._answer_from_reader_payload(payload, facts, sources)
        if not answer:
            answer = self._render_fact_card_answer(payload, facts, sources, fact_warnings)

        fact_evidence = self._evidence_from_fact_cards(facts)
        return AnswerResult(
            answer=answer,
            confidence=self._fact_confidence(payload, facts, rejected_count, fact_warnings),
            sources=self._source_hits_from_fact_cards(facts, sources),
            evidence_notes=[fact["claim"] for fact in facts[:5]],
            evidence=fact_evidence or evidence[:5],
            warnings=fact_warnings,
        )

    def _read_facts_with_light_readers(
        self,
        user_query: str,
        plan: QueryPlan,
        sources: list[dict[str, Any]],
        warnings: list[str],
    ) -> dict[str, Any] | None:
        tasks = self._reader_tasks(plan)
        if not tasks:
            return None
        if len(tasks) == 1:
            result = self._read_task_with_ai(user_query, plan, sources, warnings, tasks[0])
            return self._combine_reader_results([result], sources) if result else None

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_READERS, len(tasks))) as executor:
            futures = [
                executor.submit(self._read_task_with_ai, user_query, plan, sources, warnings, task)
                for task in tasks[:MAX_PARALLEL_READERS]
            ]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)
        return self._combine_reader_results(results, sources) if results else None

    def _read_task_with_ai(
        self,
        user_query: str,
        plan: QueryPlan,
        sources: list[dict[str, Any]],
        warnings: list[str],
        task: str,
    ) -> dict[str, Any] | None:
        system = LIGHT_READER_SYSTEMS.get(task)
        if not system:
            return None
        try:
            response = self.client.chat.completions.create(
                model=settings.ai_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "user_query": user_query,
                                "current_date": date.today().isoformat(),
                                "query_plan": plan.model_dump(mode="json"),
                                "reader_task": task,
                                "requested_slots": self._requested_slots(plan),
                                "sources": [
                                    self._public_source_payload(
                                        self._task_focused_source_payload(source, task),
                                        include_context=True,
                                    )
                                    for source in sources
                                ],
                                "warnings": warnings,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            )
            payload = self._parse_json_object(response.choices[0].message.content or "")
        except Exception:
            return None
        if not payload:
            return None
        payload["task"] = str(payload.get("task") or task)
        return payload

    def _combine_reader_results(self, results: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict[str, Any]:
        ordered_tasks = ["deadline", "eligibility", "process", "material", "comparison", "general"]
        results = sorted(
            [result for result in results if isinstance(result, dict)],
            key=lambda item: ordered_tasks.index(str(item.get("task"))) if str(item.get("task")) in ordered_tasks else 99,
        )
        facts: list[dict[str, Any]] = []
        missing: list[str] = []
        warnings: list[str] = []
        sections: list[tuple[str, str]] = []
        confidences: list[str] = []
        for result in results:
            task = str(result.get("task") or "general")
            section = self._clean_multiline(str(result.get("answer_section") or ""), max_chars=1200)
            if section and section not in [item[1] for item in sections]:
                sections.append((task, section))
            raw_facts = result.get("facts")
            if isinstance(raw_facts, list):
                facts.extend(item for item in raw_facts if isinstance(item, dict))
            missing.extend(str(item) for item in result.get("missing", []) if str(item).strip())
            warnings.extend(str(item) for item in result.get("warnings", []) if str(item).strip())
            confidence = str(result.get("confidence") or "").lower()
            if confidence in {"high", "medium", "low", "none"}:
                confidences.append(confidence)

        final_answer = self._render_parallel_reader_answer(sections, facts, sources, missing, warnings)
        direct_answer = sections[0][1] if sections else ""
        return {
            "final_answer": final_answer,
            "direct_answer": direct_answer,
            "confidence": self._combine_confidence(confidences, facts),
            "facts": facts,
            "missing": self._dedupe_strings(missing),
            "warnings": self._dedupe_strings(warnings),
        }

    def _render_parallel_reader_answer(
        self,
        sections: list[tuple[str, str]],
        raw_facts: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        missing: list[str],
        warnings: list[str],
    ) -> str:
        cleaned_sections: list[tuple[str, str]] = []
        for task, section in sections:
            text = re.sub(r"^#+\s*", "", section).strip()
            text = re.sub(r"^[-•]\s*", "", text).strip()
            if text:
                cleaned_sections.append((task, text))

        lead = self._direct_lead_from_sections(cleaned_sections) or self._direct_lead_from_raw_facts(raw_facts)
        if not lead:
            lead = "结论：已找到相关官网材料，但没有抽取到足够明确的直接结论。"
        lead = re.sub(r"^\*\*(.*?)\*\*$", r"\1", lead).strip()
        if not lead.startswith("结论"):
            lead = f"结论：{lead}"

        lines = [f"**{lead}**"]
        labels = {
            "deadline": "时间信息",
            "eligibility": "资格与限制",
            "process": "办理流程",
            "material": "材料与附件",
            "comparison": "差异对比",
            "general": "补充说明",
        }
        for task, section in cleaned_sections:
            body = re.sub(r"^\*\*.*?\*\*\s*", "", section, count=1).strip()
            if not body:
                match = re.search(r"\*\*(.*?)\*\*", section)
                body = match.group(1).strip() if match else section
            body = body.strip()
            if not body:
                continue
            lines.extend(["", labels.get(task, "依据"), body])

        deduped_missing = self._dedupe_strings(missing)
        if deduped_missing:
            lines.append("")
            lines.append("未找到明确依据")
            lines.extend(f"- {item}" for item in deduped_missing[:4])

        deduped_warnings = self._dedupe_strings(warnings)
        if deduped_warnings:
            lines.append("")
            lines.append("注意")
            lines.extend(f"- {item}" for item in deduped_warnings[:3])

        used_refs = []
        for fact in raw_facts:
            ref = str(fact.get("source_ref") or "").strip()
            if ref and ref not in used_refs:
                used_refs.append(ref)
        if not used_refs:
            for _, section in cleaned_sections:
                for ref in re.findall(r"\[\d+\]", section):
                    if ref not in used_refs:
                        used_refs.append(ref)

        lines.extend(["", "参考信息源："])
        for source in sources:
            if used_refs and source["ref"] not in used_refs:
                continue
            date_part = f"，{source.get('publish_date')}" if source.get("publish_date") else ""
            lines.append(f"{source['ref']} {source.get('source')}：《{source.get('title')}》{date_part}，{source.get('url')}")
        return "\n".join(lines)

    @staticmethod
    def _direct_lead_from_sections(sections: list[tuple[str, str]]) -> str:
        for _, section in sections:
            match = re.search(r"\*\*(.*?)\*\*", section)
            if match:
                return match.group(1).strip()
            first = re.split(r"\n|。", section, maxsplit=1)[0].strip()
            if first:
                return first
        return ""

    @staticmethod
    def _direct_lead_from_raw_facts(raw_facts: list[dict[str, Any]]) -> str:
        for fact in raw_facts:
            if fact.get("is_direct") and fact.get("claim"):
                return str(fact.get("claim"))
        for fact in raw_facts:
            if fact.get("claim"):
                return str(fact.get("claim"))
        return ""

    @staticmethod
    def _combine_confidence(confidences: list[str], facts: list[dict[str, Any]]) -> str:
        values = [item for item in confidences if item in {"high", "medium", "low", "none"}]
        if not values:
            return "medium" if facts else "none"
        if all(item == "none" for item in values):
            return "none"
        if all(item == "high" for item in values):
            return "high"
        if "medium" in values or "high" in values:
            return "medium"
        return "low"

    def _reader_tasks(self, plan: QueryPlan) -> list[str]:
        if plan.intent in {"find_document", "attachment_query", "latest_updates", "unknown"}:
            return []
        slots = set(self._requested_slots(plan))
        tasks: list[str] = []
        if plan.intent == "deadline_query" or "time" in slots:
            tasks.append("deadline")
        if plan.intent == "eligibility_query" or {"audience", "condition", "exception"} & slots:
            tasks.append("eligibility")
        if plan.intent == "process_guide" or {"process", "entry"} & slots:
            tasks.append("process")
        if "material" in slots:
            tasks.append("material")
        if "comparison" in slots:
            tasks.append("comparison")
        if not tasks:
            tasks.append("general")
        return self._dedupe_strings(tasks)[:MAX_PARALLEL_READERS]

    def _read_facts_with_ai(
        self,
        user_query: str,
        plan: QueryPlan,
        sources: list[dict[str, Any]],
        evidence: list[EvidenceItem],
        warnings: list[str],
    ) -> dict[str, Any] | None:
        try:
            response = self.client.chat.completions.create(
                model=settings.ai_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": FACT_EXTRACTOR_SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "user_query": user_query,
                                "current_date": date.today().isoformat(),
                                "query_plan": plan.model_dump(mode="json"),
                                "requested_slots": self._requested_slots(plan),
                                "sources": [self._public_source_payload(source, include_context=True) for source in sources],
                                "evidence": [item.model_dump(mode="json") for item in evidence[:6]],
                                "warnings": warnings,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            )
            return self._parse_json_object(response.choices[0].message.content or "")
        except Exception:
            return None

    def _source_payload_for_fact_extraction(
        self,
        plan: QueryPlan,
        hits: list[SearchHit],
    ) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for index, hit in enumerate(hits[:FACT_SOURCE_LIMIT], start=1):
            max_chars = FACT_PRIMARY_CONTEXT_CHARS if index <= 2 else FACT_SECONDARY_CONTEXT_CHARS
            context = self._source_context_for_reader(hit, plan, max_chars=max_chars)
            attachments = [
                {"name": item.get("name"), "url": item.get("url")}
                for item in hit.attachments[:8]
                if item.get("name") or item.get("url")
            ]
            ai_metadata_hint = self._ai_metadata_hint(hit.id)
            sources.append(
                {
                    "ref": f"[{index}]",
                    "title": hit.title,
                    "source": hit.source,
                    "category": hit.category,
                    "publish_date": hit.publish_date,
                    "url": hit.url,
                    "snippet": self._clean(hit.snippet, max_chars=520),
                    "context": context,
                    "heading": hit.heading,
                    "page": hit.page,
                    "attachment_name": hit.attachment_name,
                    "chunk_kind": hit.chunk_kind,
                    "relevance_note": hit.relevance_note,
                    "attachments": attachments,
                    "deadline": hit.deadline,
                    "applicable_colleges": hit.applicable_colleges,
                    "applicable_grades": hit.applicable_grades,
                    "student_types": hit.student_types,
                    "topics": hit.topics,
                    "keywords": hit.keywords,
                    "ai_metadata_hint": ai_metadata_hint,
                    "_hit": hit,
                    "_plan": plan,
                    "_support_text": self._source_support_text(hit, context, attachments),
                }
            )
        return sources

    def _task_focused_source_payload(self, source: dict[str, Any], task: str) -> dict[str, Any]:
        hit = source.get("_hit")
        plan = source.get("_plan")
        focused = dict(source)
        if isinstance(hit, SearchHit) and isinstance(plan, QueryPlan):
            max_chars = FACT_PRIMARY_CONTEXT_CHARS if source.get("ref") in {"[1]", "[2]"} else FACT_SECONDARY_CONTEXT_CHARS
            focused["context"] = self._source_context_for_reader_task(hit, plan, task, max_chars=max_chars)
        return focused

    def _source_context_for_reader(self, hit: SearchHit, plan: QueryPlan, max_chars: int) -> str:
        return self._source_context_for_reader_task(hit, plan, "general", max_chars)

    def _source_context_for_reader_task(self, hit: SearchHit, plan: QueryPlan, task: str, max_chars: int) -> str:
        terms = self._context_terms(plan, hit)
        task_terms = self._task_context_terms(task)
        focused_terms = self._dedupe_strings([*task_terms, *terms])[:40]
        header_parts = [
            f"标题：{hit.title}",
            f"来源：{hit.source}",
            f"栏目：{hit.category or ''}",
            f"发布时间：{hit.publish_date or ''}",
        ]
        if hit.relevance_note:
            header_parts.append(f"检索判断：{hit.relevance_note}")

        attachment_block = ""
        if hit.attachments:
            attachment_lines = []
            for item in hit.attachments[:8]:
                name = item.get("name") or "附件"
                url = item.get("url") or ""
                attachment_lines.append(f"{name} {url}".strip())
            attachment_block = "附件列表：\n" + "\n".join(attachment_lines)

        matched_block = ""
        if hit.matched_chunk_text:
            label = "命中附件片段" if hit.attachment_name else "命中正文片段"
            detail = f"附件《{hit.attachment_name}》" if hit.attachment_name else ""
            page = f"第{hit.page}页" if hit.page else ""
            matched_block = f"{label}：{detail}{page} {self._clean(hit.matched_chunk_text, max_chars=520)}".strip()

        focused_body_block = ""
        related_chunks_block = ""
        body_intro_block = ""
        snippet_block = ""
        row = self.store.get_document(hit.id)
        if row:
            body_text = self._strip_embedded_attachment_text(row["body"] or "")
            focused_body = self._focused_body_context(body_text, focused_terms, max_chars=620)
            if focused_body:
                focused_body_block = "文章正文相关窗口：\n" + focused_body

            related_chunks = self._related_chunk_context(hit.id, focused_terms, task=task, max_chars=650)
            if related_chunks:
                related_chunks_block = "同篇文章相关文本块：\n" + related_chunks

            body_intro = self._clean(body_text, max_chars=220)
            if body_intro and self._normalize_quote_text(body_intro[:80]) not in self._normalize_quote_text(focused_body):
                body_intro_block = "文章开头：\n" + body_intro
        elif hit.snippet:
            snippet_block = "检索摘要：\n" + self._clean(hit.snippet, max_chars=480)

        evidence_blocks = self._ordered_context_blocks(
            plan,
            hit,
            task=task,
            matched_block=matched_block,
            focused_body_block=focused_body_block,
            related_chunks_block=related_chunks_block,
            attachment_block=attachment_block,
            body_intro_block=body_intro_block,
            snippet_block=snippet_block,
        )
        parts = [*header_parts, *evidence_blocks]
        return self._trim_context("\n\n".join(part for part in parts if part.strip()), max_chars=max_chars)

    def _ai_metadata_hint(self, doc_id: int) -> dict[str, Any]:
        row = self.store.get_document(doc_id)
        if not row or "ai_metadata_json" not in row.keys():
            return {}
        try:
            data = json.loads(row["ai_metadata_json"] or "{}")
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
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
        hint = {key: value for key, value in data.items() if key in allowed and value}
        if isinstance(hint.get("attachment_summaries"), list):
            hint["attachment_summaries"] = hint["attachment_summaries"][:6]
        return hint

    def _related_chunk_context(self, doc_id: int, terms: list[str], task: str, max_chars: int) -> str:
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
            return ""
        scored: list[tuple[int, int, int, str]] = []
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
            if self._chunk_matches_task(haystack, task):
                score += 5
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
            label_parts = [
                chunk_kind or "正文",
                str(row["heading"] or ""),
                f"附件《{row['attachment_name']}》" if row["attachment_name"] else "",
                f"第{row['page']}页" if row["page"] else "",
            ]
            label = " / ".join(part for part in label_parts if part)
            chunk = f"[{label}] {self._clean(text, max_chars=320)}"
            scored.append((score, type_priority, -position, chunk))
        scored.sort(reverse=True)
        selected: list[str] = []
        seen: set[str] = set()
        total = 0
        attachment_text_count = 0
        for _, _, _, chunk in scored:
            if chunk.startswith("[attachment_text"):
                if attachment_text_count >= (3 if task == "material" else 1):
                    continue
                attachment_text_count += 1
            key = self._normalize_quote_text(chunk[:120])
            if key in seen:
                continue
            seen.add(key)
            if total + len(chunk) > max_chars and selected:
                break
            selected.append(chunk)
            total += len(chunk)
            if len(selected) >= 5:
                break
        return "\n".join(selected)

    def _focused_body_context(self, body: str, terms: list[str], max_chars: int) -> str:
        body = re.sub(r"<[^>]+>", "", body or "")
        if not body.strip():
            return ""
        usable_terms = self._usable_terms(terms)
        if not usable_terms:
            return ""

        windows: list[tuple[int, int, str]] = []
        for term in usable_terms[:28]:
            for match in re.finditer(re.escape(term), body, flags=re.IGNORECASE):
                start = max(0, match.start() - FACT_BODY_WINDOW_CHARS // 2)
                end = min(len(body), match.end() + FACT_BODY_WINDOW_CHARS)
                snippet = re.sub(r"\s+", " ", body[start:end]).strip()
                if not snippet:
                    continue
                score = sum(1 for item in usable_terms if item and item in snippet)
                windows.append((score, -start, snippet))
                break

        if not windows:
            return ""
        windows.sort(reverse=True)
        selected: list[str] = []
        seen: set[str] = set()
        total = 0
        for _, _, snippet in windows:
            key = self._normalize_quote_text(snippet[:120])
            if key in seen:
                continue
            seen.add(key)
            line = f"- {snippet}"
            if total + len(line) > max_chars and selected:
                break
            selected.append(line)
            total += len(line)
            if len(selected) >= 3:
                break
        return "\n".join(selected)[:max_chars]

    @staticmethod
    def _strip_embedded_attachment_text(body: str) -> str:
        text = body or ""
        match = re.search(r"\n\s*(?:附件正文摘录[:：]|附件《[^》]+》正文摘录[:：])", text)
        if match:
            return text[: match.start()].strip()
        return text.strip()

    def _ordered_context_blocks(
        self,
        plan: QueryPlan,
        hit: SearchHit,
        *,
        task: str,
        matched_block: str,
        focused_body_block: str,
        related_chunks_block: str,
        attachment_block: str,
        body_intro_block: str,
        snippet_block: str,
    ) -> list[str]:
        """Order evidence by the user's requested answer shape."""
        if task == "deadline":
            return [matched_block, focused_body_block, related_chunks_block, snippet_block, attachment_block, body_intro_block]
        if task == "eligibility":
            return [focused_body_block, related_chunks_block, matched_block, attachment_block, snippet_block, body_intro_block]
        if task == "process":
            return [focused_body_block, related_chunks_block, matched_block, snippet_block, attachment_block, body_intro_block]
        if task == "material":
            return [attachment_block, matched_block, related_chunks_block, focused_body_block, snippet_block, body_intro_block]
        if task == "comparison":
            return [matched_block, focused_body_block, related_chunks_block, attachment_block, snippet_block, body_intro_block]
        if self._attachment_context_first(plan):
            if hit.attachment_name:
                return [
                    matched_block,
                    attachment_block,
                    related_chunks_block,
                    focused_body_block,
                    snippet_block,
                    body_intro_block,
                ]
            return [
                attachment_block,
                matched_block,
                related_chunks_block,
                focused_body_block,
                snippet_block,
                body_intro_block,
            ]

        return [
            matched_block,
            focused_body_block,
            related_chunks_block,
            attachment_block,
            snippet_block,
            body_intro_block,
        ]

    @staticmethod
    def _attachment_context_first(plan: QueryPlan) -> bool:
        if plan.intent == "attachment_query":
            return True
        raw_slots = plan.entities.get("requested_slots")
        slots = {str(item) for item in raw_slots} if isinstance(raw_slots, list) else set()
        return bool({"material", "entry"} & slots and not {"time", "condition", "process", "audience"} & slots)

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
        values.extend(Answerer._slot_context_terms(plan))
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
        return terms[:32]

    @staticmethod
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

    @staticmethod
    def _task_context_terms(task: str) -> list[str]:
        aliases = {
            "deadline": ["报名时间", "申请时间", "截止", "起止", "时间为", "日期", "办理时间", "已截止"],
            "eligibility": ["对象", "范围", "条件", "要求", "资格", "限制", "不得", "不允许", "学院", "年级"],
            "process": ["流程", "步骤", "入口", "系统", "平台", "提交", "登录", "办理", "信息门户"],
            "material": ["附件", "材料", "申请表", "表格", "下载", "证明", "名单", "PDF", "doc", "xls"],
            "comparison": ["不同", "区别", "差异", "相比", "教务处", "学院"],
            "general": ["结论", "说明", "要求", "时间", "来源"],
        }
        return aliases.get(task, aliases["general"])

    @staticmethod
    def _chunk_matches_task(text: str, task: str) -> bool:
        patterns = {
            "deadline": r"(报名时间|申请时间|截止|起止|时间为|日期|办理时间|已截止)",
            "eligibility": r"(对象|范围|条件|要求|资格|限制|不得|不允许|学院|年级)",
            "process": r"(流程|步骤|入口|系统|平台|提交|登录|办理|信息门户)",
            "material": r"(附件|材料|申请表|表格|下载|证明|名单|PDF|pdf|doc|xls)",
            "comparison": r"(不同|区别|差异|相比|教务处|学院)",
        }
        pattern = patterns.get(task)
        return bool(pattern and re.search(pattern, text, flags=re.IGNORECASE))

    @staticmethod
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

    @staticmethod
    def _public_source_payload(source: dict[str, Any], include_context: bool) -> dict[str, Any]:
        public = {key: value for key, value in source.items() if not key.startswith("_")}
        if not include_context:
            public.pop("context", None)
            public.pop("snippet", None)
            public.pop("ai_metadata_hint", None)
        return public

    def _answer_from_reader_payload(
        self,
        payload: dict[str, Any],
        facts: list[dict[str, Any]],
        sources: list[dict[str, Any]],
    ) -> str | None:
        answer = self._clean_multiline(str(payload.get("final_answer") or ""), max_chars=5000)
        if not answer:
            return None
        answer = self._ensure_answer_references(answer, facts)
        if not re.search(r"参考信息源", answer):
            lines = [answer.rstrip(), "", "参考信息源："]
            for source in self._used_sources(facts, sources):
                date_part = f"，{source.get('publish_date')}" if source.get("publish_date") else ""
                lines.append(f"{source['ref']} {source.get('source')}：《{source.get('title')}》{date_part}，{source.get('url')}")
            answer = "\n".join(lines)
        return answer

    @staticmethod
    def _ensure_answer_references(answer: str, facts: list[dict[str, Any]]) -> str:
        if re.search(r"\[\d+\]", answer):
            return answer
        refs = []
        for fact in facts:
            ref = str(fact.get("source_ref") or "").strip()
            if ref and ref not in refs:
                refs.append(ref)
        if not refs:
            return answer
        lines = answer.splitlines()
        if lines:
            lines[0] = f"{lines[0].rstrip()} {' '.join(refs[:2])}"
        return "\n".join(lines)

    def _normalize_fact_cards(
        self,
        payload: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        by_ref = {str(source["ref"]): source for source in sources}
        raw_facts = payload.get("facts")
        if not isinstance(raw_facts, list):
            return [], 0

        validated: list[dict[str, Any]] = []
        rejected = 0
        seen: set[tuple[str, str, str]] = set()
        for raw in raw_facts[: FACT_MAX_CARDS * 2]:
            if not isinstance(raw, dict):
                rejected += 1
                continue
            ref = str(raw.get("source_ref") or "").strip()
            source = by_ref.get(ref)
            slot = str(raw.get("slot") or "other").strip().lower()
            claim = self._clean(str(raw.get("claim") or ""), max_chars=260)
            quote = self._clean(str(raw.get("quote") or ""), max_chars=520)
            fact_confidence = self._normalize_fact_confidence(raw.get("confidence"))
            if slot not in FACT_ALLOWED_SLOTS:
                slot = "other"
            if not source or not claim:
                rejected += 1
                continue
            if not quote:
                quote = self._fallback_fact_quote(claim, source)
            evidence_type = self._verified_evidence_type(raw.get("evidence_type"), source, quote)
            key = (slot, ref, self._normalize_quote_text(quote))
            if key in seen:
                continue
            seen.add(key)
            validated.append(
                {
                    "slot": slot,
                    "claim": claim,
                    "source_ref": ref,
                    "quote": quote,
                    "is_direct": bool(raw.get("is_direct")),
                    "confidence": fact_confidence,
                    "evidence_type": evidence_type,
                    "reason": self._clean(str(raw.get("reason") or ""), max_chars=180),
                    "_source": source,
                }
            )
            if len(validated) >= FACT_MAX_CARDS:
                break
        validated.sort(
            key=lambda fact: (
                bool(fact.get("is_direct")),
                float(fact.get("confidence") or 0),
                FACT_EVIDENCE_TYPE_PRIORITY.get(str(fact.get("evidence_type") or "unknown"), 0),
            ),
            reverse=True,
        )
        return validated, rejected

    def _validate_fact_cards(
        self,
        payload: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        return self._normalize_fact_cards(payload, sources)

    def _fallback_fact_quote(self, claim: str, source: dict[str, Any]) -> str:
        for value in (
            source.get("snippet"),
            source.get("title"),
            source.get("publish_date"),
            source.get("context"),
        ):
            text = self._clean(str(value or ""), max_chars=220)
            if text:
                return text
        for item in source.get("attachments") or []:
            text = self._clean(" ".join(str(item.get(key) or "") for key in ("name", "url")), max_chars=220)
            if text:
                return text
        return self._clean(claim, max_chars=220)

    def _render_fact_card_answer(
        self,
        payload: dict[str, Any],
        facts: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        direct = self._clean(str(payload.get("direct_answer") or ""), max_chars=180)
        if not direct:
            direct_fact = next((fact for fact in facts if fact.get("is_direct") or fact["slot"] == "answer"), facts[0])
            direct = direct_fact["claim"]
        direct = re.sub(r"^\*\*(.*?)\*\*$", r"\1", direct).strip(" 。")
        if not direct.startswith("结论"):
            direct = f"结论：{direct}"

        lines = [f"**{direct}**"]
        for slot in self._ordered_fact_slots(facts):
            slot_facts = [fact for fact in facts if fact["slot"] == slot]
            if not slot_facts:
                continue
            lines.append("")
            lines.append(FACT_SLOT_LABELS.get(slot, "依据"))
            for fact in slot_facts[:4]:
                quality = self._fact_quality_text(fact)
                lines.append(f"- {fact['claim']} {fact['source_ref']}（{quality}；原文：{self._short_quote(fact['quote'])}）")

        missing = self._dedupe_strings([str(item) for item in payload.get("missing", []) if str(item).strip()])
        if missing:
            lines.append("")
            lines.append("仍缺信息")
            lines.extend(f"- {item}" for item in missing[:4])

        if warnings:
            lines.append("")
            lines.append("注意")
            lines.extend(f"- {item}" for item in warnings[:3])

        lines.append("")
        lines.append("参考信息源：")
        for source in self._used_sources(facts, sources):
            date_part = f"，{source.get('publish_date')}" if source.get("publish_date") else ""
            lines.append(f"{source['ref']} {source.get('source')}：《{source.get('title')}》{date_part}，{source.get('url')}")
        return "\n".join(lines)

    @staticmethod
    def _ordered_fact_slots(facts: list[dict[str, Any]]) -> list[str]:
        order = ["answer", "time", "audience", "condition", "process", "material", "entry", "exception", "comparison", "source", "other"]
        existing = {fact["slot"] for fact in facts}
        return [slot for slot in order if slot in existing]

    @staticmethod
    def _requested_slots(plan: QueryPlan) -> list[str]:
        raw_slots = plan.entities.get("requested_slots")
        if isinstance(raw_slots, list):
            slots = [str(item).strip() for item in raw_slots if str(item).strip() in FACT_ALLOWED_SLOTS]
            if slots:
                return list(dict.fromkeys(slots))
        return ["answer", "source"]

    def _evidence_from_fact_cards(self, facts: list[dict[str, Any]]) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        for fact in facts[:8]:
            source = fact.get("_source")
            hit = source.get("_hit") if isinstance(source, dict) else None
            if not isinstance(hit, SearchHit):
                continue
            evidence.append(
                EvidenceItem(
                    ref=fact["source_ref"],
                    source_id=hit.id,
                    title=hit.title,
                    url=hit.url,
                    quote=fact["quote"],
                    reason=(
                        f"{FACT_SLOT_LABELS.get(fact['slot'], '事实依据')}："
                        f"{fact.get('reason') or fact['claim']}；{self._fact_quality_text(fact)}"
                    ),
                    source=hit.source,
                    publish_date=hit.publish_date,
                    heading=hit.heading,
                    page=hit.page,
                    attachment_name=hit.attachment_name,
                    chunk_kind=hit.chunk_kind,
                    evidence_type=fact.get("evidence_type"),
                    fact_confidence=fact.get("confidence"),
                    attachments=hit.attachments[:4],
                )
            )
        return evidence

    @staticmethod
    def _source_hits_from_fact_cards(facts: list[dict[str, Any]], sources: list[dict[str, Any]]) -> list[SearchHit]:
        used_refs = {fact["source_ref"] for fact in facts}
        hits: list[SearchHit] = []
        seen_ids: set[int] = set()
        for source in sources:
            if source["ref"] not in used_refs:
                continue
            hit = source.get("_hit")
            if isinstance(hit, SearchHit) and hit.id not in seen_ids:
                seen_ids.add(hit.id)
                hits.append(hit)
        return hits

    @staticmethod
    def _used_sources(facts: list[dict[str, Any]], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        used_refs = {fact["source_ref"] for fact in facts}
        return [source for source in sources if source["ref"] in used_refs]

    def _fact_confidence(
        self,
        payload: dict[str, Any],
        facts: list[dict[str, Any]],
        rejected_count: int,
        warnings: list[str],
    ) -> str:
        raw = str(payload.get("confidence") or "medium").lower()
        confidence = raw if raw in {"high", "medium", "low", "none"} else "medium"
        if confidence == "none":
            return "none"
        if not any(fact.get("is_direct") or fact["slot"] == "answer" for fact in facts):
            confidence = "low"
        if rejected_count >= len(facts) * 2:
            confidence = "low"
        if any("不足" in warning or "未找到" in warning or "不匹配" in warning for warning in warnings):
            confidence = "low" if confidence != "none" else "none"
        direct_confidences = [float(fact.get("confidence") or 0) for fact in facts if fact.get("is_direct") or fact["slot"] == "answer"]
        if direct_confidences and max(direct_confidences) < 0.6:
            confidence = "low" if confidence != "none" else "none"
        if facts and all(str(fact.get("evidence_type") or "") in {"attachment", "attachment_list", "table"} for fact in facts):
            confidence = "low" if confidence == "high" else confidence
        return confidence

    def _fact_warnings(self, payload: dict[str, Any], rejected_count: int, warnings: list[str]) -> list[str]:
        values = [*warnings, *[str(item) for item in payload.get("warnings", []) if str(item).strip()]]
        if rejected_count:
            values.append(f"AI 返回的 {rejected_count} 条事实因来源编号缺失或内容为空未展示。")
        return self._dedupe_strings(values)

    @staticmethod
    def _normalize_fact_confidence(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.7
        return max(0.0, min(1.0, round(number, 3)))

    @staticmethod
    def _verified_evidence_type(value: Any, source: dict[str, Any], quote: str) -> str:
        raw = str(value or "").strip().lower()
        inferred = Answerer._infer_evidence_type(source, quote)
        if raw == inferred and raw in FACT_ALLOWED_EVIDENCE_TYPES:
            return raw
        if raw == "table" and inferred in {"attachment", "body", "mixed"}:
            return "table"
        if raw in {"attachment", "attachment_list"} and inferred in {"attachment", "attachment_list"}:
            return inferred
        if raw in {"body", "title", "publish_date"} and inferred in {"body", "title", "publish_date"}:
            return inferred
        return inferred

    @staticmethod
    def _infer_evidence_type(source: dict[str, Any], quote: str) -> str:
        quote_norm = Answerer._normalize_quote_text(quote)
        if not quote_norm:
            return "unknown"
        if quote_norm in Answerer._normalize_quote_text(str(source.get("title") or "")):
            return "title"
        if quote_norm in Answerer._normalize_quote_text(str(source.get("publish_date") or "")):
            return "publish_date"
        for item in source.get("attachments") or []:
            attachment_text = f"{item.get('name') or ''} {item.get('url') or ''}"
            if quote_norm in Answerer._normalize_quote_text(attachment_text):
                return "attachment_list"

        chunk_kind = str(source.get("chunk_kind") or "").strip().lower()
        attachment_name = str(source.get("attachment_name") or "").strip()
        context_norm = Answerer._normalize_quote_text(str(source.get("context") or source.get("_support_text") or ""))
        if chunk_kind == "title":
            return "title"
        if chunk_kind == "attachment_list":
            return "attachment_list"
        if chunk_kind == "attachment_text" or attachment_name:
            return "attachment"
        if chunk_kind == "body":
            return "body"
        if quote_norm in context_norm:
            return "mixed"
        return "unknown"

    @staticmethod
    def _fact_quality_text(fact: dict[str, Any]) -> str:
        confidence = float(fact.get("confidence") or 0)
        evidence_type = str(fact.get("evidence_type") or "unknown")
        type_labels = {
            "title": "标题证据",
            "publish_date": "发布时间证据",
            "body": "正文证据",
            "attachment": "附件证据",
            "attachment_list": "附件列表证据",
            "table": "表格证据",
            "mixed": "混合证据",
            "unknown": "未标明证据类型",
        }
        return f"{type_labels.get(evidence_type, evidence_type)}，事实置信度 {confidence:.2f}"

    def _build_evidence(self, plan: QueryPlan, hits: list[SearchHit]) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        for index, hit in enumerate(hits[:8], start=1):
            quote = self._source_context_for_reader(hit, plan, max_chars=900) or self._clean(hit.snippet)
            if not quote:
                continue
            evidence.append(
                EvidenceItem(
                    ref=f"[{index}]",
                    source_id=hit.id,
                    title=hit.title,
                    url=hit.url,
                    quote=quote,
                    reason="检索召回的候选来源片段",
                    source=hit.source,
                    publish_date=hit.publish_date,
                    heading=hit.heading,
                    page=hit.page,
                    attachment_name=hit.attachment_name,
                    chunk_kind=hit.chunk_kind,
                    attachments=hit.attachments[:4],
                )
            )
        return evidence

    def _build_warnings(self, plan: QueryPlan, hits: list[SearchHit]) -> list[str]:
        warnings: list[str] = []
        if not hits:
            return warnings
        if plan.time_scope in {"current", "recent_2y"}:
            old_docs = [hit for hit in hits[:5] if self._is_old_publish_date(hit.publish_date)]
            if old_docs:
                warnings.append("部分候选来源发布时间较早，涉及当前安排时应优先采纳较新且有直接证据的来源。")
        expired_deadlines = [hit.deadline for hit in hits[:5] if self._is_past_date(hit.deadline)]
        if expired_deadlines:
            warnings.append("部分候选来源的结构化截止日期已过，不能直接当作当前可办理依据。")
        return warnings

    def _document_list_answer(
        self,
        hits: list[SearchHit],
        evidence: list[EvidenceItem],
        warnings: list[str],
    ) -> AnswerResult:
        first = hits[0]
        lines = [f"**结论：最相关原文是《{first.title}》。**", "", "我找到这些可能相关的官网原文："]
        for index, hit in enumerate(hits[:8], start=1):
            date_part = f"，{hit.publish_date}" if hit.publish_date else ""
            category_part = f" / {hit.category}" if hit.category else ""
            lines.append(f"{index}. 《{hit.title}》")
            lines.append(f"   来源：{hit.source}{category_part}{date_part}")
            lines.append(f"   链接：{hit.url}")
            if hit.attachments:
                names = "、".join(item.get("name", "附件") for item in hit.attachments[:3])
                lines.append(f"   附件：{names}")
        if warnings:
            lines.append("")
            lines.append("提示：" + "；".join(warnings[:3]))
        return AnswerResult(
            answer="\n".join(lines),
            confidence="medium",
            sources=hits[:8],
            evidence=evidence,
            warnings=warnings,
        )

    def _ai_unavailable_answer(
        self,
        hits: list[SearchHit],
        warnings: list[str],
        reason: str,
    ) -> AnswerResult:
        lines = [
            f"**结论：{reason}**",
            "",
            "已停止生成总结，避免用规则或片段拼接出误导性答案。",
        ]
        if warnings:
            lines.append("")
            lines.append("提示：" + "；".join(warnings[:3]))
        return AnswerResult(
            answer="\n".join(lines),
            confidence="none",
            sources=[],
            evidence_notes=[],
            evidence=[],
            warnings=[reason, *warnings],
        )

    def _finalize_answer(self, result: AnswerResult, hits: list[SearchHit]) -> AnswerResult:
        if not (result.answer or "").lstrip().startswith("**"):
            lead = self._first_sentence(result.answer) or "结论：未找到足够的官网依据，不能直接给出确定答案。"
            result.answer = f"**{lead}**\n\n{result.answer}"
        return validate_answer_against_hits(result, hits)

    def _source_support_text(self, hit: SearchHit, context: str, attachments: list[dict[str, Any]]) -> str:
        parts = [
            hit.title,
            hit.source,
            hit.category or "",
            hit.publish_date or "",
            hit.url,
            hit.snippet,
            context,
            hit.heading or "",
            hit.attachment_name or "",
            hit.deadline or "",
            " ".join(hit.applicable_colleges),
            " ".join(hit.applicable_grades),
            " ".join(hit.student_types),
            " ".join(hit.topics),
            " ".join(hit.keywords),
        ]
        for item in attachments:
            parts.append(str(item.get("name") or ""))
            parts.append(str(item.get("url") or ""))
        return "\n".join(part for part in parts if part)

    def _ai_enabled(self) -> bool:
        if not self.client:
            return False
        mode = settings.ai_answer_composer_mode
        return mode not in {"off", "false", "0", "disabled"}

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any] | None:
        text = (content or "").strip()
        if not text:
            return None
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= start:
            text = text[start : end + 1]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _normalize_quote_text(text: str) -> str:
        return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", text or "")).strip()

    @staticmethod
    def _trim_context(text: str, max_chars: int) -> str:
        text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + "\n...[已截断]"

    @staticmethod
    def _clean(text: str, max_chars: int = 360) -> str:
        text = re.sub(r"<[^>]+>", "", text or "")
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]

    @staticmethod
    def _clean_multiline(text: str, max_chars: int = 5000) -> str:
        text = re.sub(r"<[^>]+>", "", text or "")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        cleaned = "\n".join(lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned[:max_chars]

    @staticmethod
    def _short_quote(quote: str, max_chars: int = 96) -> str:
        quote = re.sub(r"\s+", " ", quote or "").strip(" “”。")
        if len(quote) <= max_chars:
            return f"“{quote}”"
        return f"“{quote[:max_chars].rstrip()}...”"

    @staticmethod
    def _first_sentence(answer: str) -> str:
        text = re.sub(r"\s+", " ", answer or "").strip()
        text = re.sub(r"^\*\*(.*?)\*\*", r"\1", text)
        if not text:
            return ""
        first = re.split(r"(?<=[。！？])\s| - |\n", text, maxsplit=1)[0].strip()
        return first[:180]

    @staticmethod
    def _dedupe_strings(values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            if text and text not in seen:
                seen.add(text)
                output.append(text)
        return output

    @staticmethod
    def _is_past_date(value: str | None) -> bool:
        if not value:
            return False
        try:
            return datetime.strptime(value, "%Y-%m-%d").date() < date.today()
        except ValueError:
            return False

    @staticmethod
    def _is_old_publish_date(value: str | None) -> bool:
        if not value:
            return False
        try:
            published = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return False
        return (date.today() - published).days > 730
