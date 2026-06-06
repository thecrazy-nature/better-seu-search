from __future__ import annotations

import re

from ..models import AnswerResult, EvidenceItem, SearchHit


URL_RE = re.compile(r"https?://[^\s)）\]】>,，。；;\"']+")


def validate_answer_against_hits(result: AnswerResult, hits: list[SearchHit]) -> AnswerResult:
    """Enforce non-AI safety rules on answer metadata and URLs."""
    by_id = {hit.id: hit for hit in hits}
    allowed_urls = _allowed_urls(hits)
    warnings = list(result.warnings)

    answer, removed_urls = _remove_unknown_urls(result.answer, allowed_urls)
    if removed_urls:
        warnings.append("答案中出现未收录链接，已移除；请只使用下方官网来源链接。")

    sources = []
    seen_sources: set[int] = set()
    for source in result.sources:
        hit = by_id.get(source.id)
        if not hit or hit.id in seen_sources:
            continue
        seen_sources.add(hit.id)
        sources.append(hit)

    evidence: list[EvidenceItem] = []
    for item in result.evidence:
        hit = by_id.get(item.source_id)
        if not hit:
            continue
        evidence.append(
            item.model_copy(
                update={
                    "title": hit.title,
                    "url": hit.url,
                    "source": hit.source,
                    "publish_date": hit.publish_date,
                    "heading": hit.heading,
                    "page": hit.page,
                    "attachment_name": hit.attachment_name,
                    "chunk_kind": hit.chunk_kind,
                    "attachments": _safe_attachments(hit),
                }
            )
        )

    return result.model_copy(
        update={
            "answer": answer,
            "sources": sources,
            "evidence": evidence,
            "warnings": _dedupe(warnings),
        }
    )


def _allowed_urls(hits: list[SearchHit]) -> set[str]:
    urls = {hit.url for hit in hits if hit.url}
    for hit in hits:
        for item in hit.attachments:
            url = item.get("url")
            if isinstance(url, str) and url:
                urls.add(url)
    return urls


def _remove_unknown_urls(answer: str, allowed_urls: set[str]) -> tuple[str, list[str]]:
    removed: list[str] = []

    def replace(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(".")
        if url in allowed_urls:
            return match.group(0)
        removed.append(url)
        return "[未收录链接已移除]"

    cleaned = URL_RE.sub(replace, answer or "")
    return cleaned, _dedupe(removed)


def _safe_attachments(hit: SearchHit) -> list[dict]:
    safe_items = []
    for item in hit.attachments[:8]:
        copied = dict(item)
        if copied.get("url") and not isinstance(copied.get("url"), str):
            copied.pop("url", None)
        safe_items.append(copied)
    return safe_items


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = str(value or "").strip()
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output
