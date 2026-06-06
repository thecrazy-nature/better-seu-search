from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


Intent = Literal[
    "find_document",
    "answer_question",
    "process_guide",
    "deadline_query",
    "eligibility_query",
    "attachment_query",
    "latest_updates",
    "profile_query",
    "unknown",
]


class UserProfile(BaseModel):
    college: str | None = None
    grade: str | None = None
    student_type: str | None = None
    major: str | None = None
    campus: str | None = None


class CollectionSourceConfig(BaseModel):
    id: int | None = None
    collection_id: int | None = None
    source_name: str
    base_url: str
    seed_urls: list[str] = Field(default_factory=list)
    include_path_prefixes: list[str] = Field(default_factory=list)
    exclude_path_prefixes: list[str] = Field(default_factory=list)
    max_depth: int | None = Field(default=None, ge=0, le=8)
    max_pages: int | None = Field(default=None, ge=1, le=5000)
    days_back: int | None = Field(default=None, ge=1, le=3650)
    is_enabled: bool = True


class CollectionSummary(BaseModel):
    id: int
    name: str
    slug: str
    description: str = ""
    is_enabled: bool = True
    source_count: int = 0
    document_count: int = 0
    last_crawled_at: str | None = None
    updated_at: str | None = None


class CollectionDetail(CollectionSummary):
    sources: list[CollectionSourceConfig] = Field(default_factory=list)


class QueryPlan(BaseModel):
    intent: Intent = "answer_question"
    confidence: float = Field(default=0.5, ge=0, le=1)
    normalized_query: str
    sub_questions: list[str] = Field(default_factory=list)
    retrieval_keywords: list[str] = Field(default_factory=list)
    expanded_queries: list[str] = Field(default_factory=list)
    entities: dict[str, str | list[str] | None] = Field(default_factory=dict)
    filters: dict[str, str | list[str] | None] = Field(default_factory=dict)
    exclude_terms: list[str] = Field(default_factory=list)
    time_scope: str | None = None
    authority_preference: str | None = None
    need_answer_summary: bool = True
    output_preset: str = "sourced_answer"
    notes: str | None = None


class SourceDocument(BaseModel):
    id: int | None = None
    title: str
    url: str
    source: str
    category: str | None = None
    publish_date: date | None = None
    body: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    applicable_colleges: list[str] = Field(default_factory=list)
    applicable_grades: list[str] = Field(default_factory=list)
    student_types: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    deadline: date | None = None
    content_hash: str | None = None


class SearchHit(BaseModel):
    id: int
    title: str
    url: str
    source: str
    category: str | None = None
    publish_date: str | None = None
    snippet: str
    score: float
    relevance_note: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    applicable_colleges: list[str] = Field(default_factory=list)
    applicable_grades: list[str] = Field(default_factory=list)
    student_types: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    deadline: str | None = None
    heading: str | None = None
    page: int | None = None
    attachment_name: str | None = None
    chunk_kind: str | None = None
    chunk_tags: list[str] = Field(default_factory=list)
    matched_chunk_text: str | None = None
    evidence_judge_label: str | None = None
    evidence_judge_confidence: float | None = None
    evidence_judge_reason: str | None = None
    evidence_judge_answerable_slots: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    ref: str
    source_id: int
    title: str
    url: str
    quote: str
    reason: str
    source: str | None = None
    publish_date: str | None = None
    heading: str | None = None
    page: int | None = None
    attachment_name: str | None = None
    chunk_kind: str | None = None
    evidence_type: str | None = None
    fact_confidence: float | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class AnswerResult(BaseModel):
    answer: str
    confidence: Literal["high", "medium", "low", "none"] = "low"
    sources: list[SearchHit] = Field(default_factory=list)
    evidence_notes: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
