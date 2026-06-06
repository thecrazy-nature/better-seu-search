from __future__ import annotations

import math
import re
from datetime import date, datetime

from ..models import QueryPlan, SearchHit, UserProfile
from ..storage import DocumentStore, row_to_hit
from ..embeddings import cosine_similarity, embed_text, embedding_json_matches_current, vector_from_json
from .synonyms import expand_terms


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")
CURRENT_SCOPE_DAYS = 730
VERY_OLD_SCOPE_DAYS = 1460
VECTOR_SCAN_LIMIT = 1200
VECTOR_CANDIDATE_LIMIT = 24
VECTOR_MIN_SIMILARITY = 0.18
GENERIC_MATCH_TERMS = {
    "通知",
    "公告",
    "文件",
    "链接",
    "原文",
    "报名",
    "申请",
    "打印",
    "成绩",
    "附件",
    "下载",
    "本科生",
    "研究生",
    "教务处",
    "时间",
    "查询",
    "办理",
    "PDF",
    "pdf",
}


def _fts_query(text: str) -> str:
    tokens = TOKEN_RE.findall(text)
    if not tokens:
        tokens = [text.strip()]
    cleaned = []
    for token in tokens[:12]:
        token = token.replace('"', " ").strip()
        if token:
            cleaned.append(f'"{token}"')
    return " OR ".join(cleaned)


def _contains_any(values: list[str], candidate: str | None) -> bool:
    if not candidate:
        return False
    return any(candidate in value or value in candidate for value in values if value)


def _recency_bonus(publish_date: str | None) -> float:
    if not publish_date:
        return 0
    try:
        days = (date.today() - datetime.strptime(publish_date, "%Y-%m-%d").date()).days
    except ValueError:
        return 0
    if days < 0:
        return 0.2
    return max(0.0, 1.5 - math.log10(days + 1))


def _deadline_bonus(deadline: str | None) -> float:
    if not deadline:
        return 0
    try:
        days = (datetime.strptime(deadline, "%Y-%m-%d").date() - date.today()).days
    except ValueError:
        return 0
    if days < 0:
        return -1.2
    if days <= 14:
        return 1.0
    if days <= 45:
        return 0.4
    return 0


def _publish_age_days(publish_date: str | None) -> int | None:
    if not publish_date:
        return None
    try:
        return (date.today() - datetime.strptime(publish_date, "%Y-%m-%d").date()).days
    except ValueError:
        return None


def _time_scope_is_currentish(scope: str | None) -> bool:
    return scope in {"current", "recent_2y"}


class SearchEngine:
    def __init__(self, store: DocumentStore | None = None) -> None:
        self.store = store or DocumentStore()
        self.source_profiles: dict[str, dict] = {}

    def search(self, plan: QueryPlan, profile: UserProfile | None = None, limit: int = 10) -> list[SearchHit]:
        profile = profile or UserProfile()
        self.source_profiles = self.store.get_source_profiles()
        query_candidates = self._query_candidates(plan)
        rows_by_id: dict[int, tuple[dict, float]] = {}

        with self.store.connect() as conn:
            for query in query_candidates[:16]:
                fts = _fts_query(query)
                if not fts:
                    continue
                try:
                    rows = conn.execute(
                        """
                        SELECT d.*,
                               c.chunk_text AS chunk_text,
                               c.heading AS heading,
                               c.page AS page,
                               c.attachment_name AS attachment_name,
                               c.chunk_kind AS chunk_kind,
                               c.tags_json AS tags_json,
                               bm25(document_chunks_fts, 4.0, 1.5, 1.0, 0.5, 0.8) AS rank,
                               snippet(document_chunks_fts, 2, '<mark>', '</mark>', '...', 44) AS snip
                        FROM document_chunks_fts
                        JOIN document_chunks c ON c.id = document_chunks_fts.rowid
                        JOIN documents d ON d.id = c.document_id
                        WHERE document_chunks_fts MATCH ?
                        ORDER BY rank
                        LIMIT 60
                        """,
                        (fts,),
                    ).fetchall()
                except Exception:
                    rows = []
                for row in rows:
                    base_score = max(0.1, -float(row["rank"])) + 0.4
                    snippet = row["snip"] or row["chunk_text"] or row["body"][:180]
                    hit_dict = row_to_hit(row, base_score, snippet)
                    score = self._score(hit_dict, plan, profile, base_score)
                    old = rows_by_id.get(hit_dict["id"])
                    if old is None or score > old[1]:
                        rows_by_id[hit_dict["id"]] = (hit_dict, score)

            for query in query_candidates[:16]:
                fts = _fts_query(query)
                if not fts:
                    continue
                try:
                    rows = conn.execute(
                        """
                        SELECT d.*,
                               bm25(documents_fts, 4.0, 1.0, 0.5, 0.5, 0.8, 0.8) AS rank,
                               snippet(documents_fts, 1, '<mark>', '</mark>', '...', 36) AS snip
                        FROM documents_fts
                        JOIN documents d ON d.id = documents_fts.rowid
                        WHERE documents_fts MATCH ?
                        ORDER BY rank
                        LIMIT 40
                        """,
                        (fts,),
                    ).fetchall()
                except Exception:
                    rows = []
                for row in rows:
                    base_score = max(0.1, -float(row["rank"]))
                    hit_dict = row_to_hit(row, base_score, row["snip"] or row["body"][:180])
                    score = self._score(hit_dict, plan, profile, base_score)
                    old = rows_by_id.get(hit_dict["id"])
                    if old is None or score > old[1]:
                        rows_by_id[hit_dict["id"]] = (hit_dict, score)

            like_terms = self._like_terms(plan, query_candidates)
            for term in like_terms[:16]:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM documents
                    WHERE title LIKE ?
                       OR body LIKE ?
                       OR attachments_json LIKE ?
                       OR topics_json LIKE ?
                       OR keywords_json LIKE ?
                    ORDER BY publish_date DESC, id DESC
                    LIMIT 30
                    """,
                    (f"%{term}%", f"%{term}%", f"%{term}%", f"%{term}%", f"%{term}%"),
                ).fetchall()
                for row in rows:
                    snippet = self._make_snippet(row["body"], term)
                    base_score = 2.0 if term in (row["title"] or "") else 0.8
                    hit_dict = row_to_hit(row, base_score, snippet)
                    score = self._score(hit_dict, plan, profile, base_score)
                    old = rows_by_id.get(hit_dict["id"])
                    if old is None or score > old[1]:
                        rows_by_id[hit_dict["id"]] = (hit_dict, score)

            if not rows_by_id and plan.intent == "latest_updates":
                rows = conn.execute(
                    """
                    SELECT * FROM documents
                    ORDER BY publish_date DESC NULLS LAST, id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                for row in rows:
                    hit_dict = row_to_hit(row, 1.0, row["body"][:180])
                    rows_by_id[hit_dict["id"]] = (hit_dict, self._score(hit_dict, plan, profile, 1.0))

            if plan.intent == "profile_query" and len(rows_by_id) < max(4, limit // 2):
                for hit_dict, score in self._profile_candidates(conn, plan, profile, limit=limit * 2):
                    old = rows_by_id.get(hit_dict["id"])
                    if old is None or score > old[1]:
                        rows_by_id[hit_dict["id"]] = (hit_dict, score)

            if self._should_use_vector_recall(plan):
                for hit_dict, score in self._vector_candidates(conn, plan, profile, limit=VECTOR_CANDIDATE_LIMIT):
                    old = rows_by_id.get(hit_dict["id"])
                    if old is None or score > old[1]:
                        rows_by_id[hit_dict["id"]] = (hit_dict, score)

        ranked = self._dedupe_ranked(sorted(rows_by_id.values(), key=lambda pair: pair[1], reverse=True))
        ranked = self._filter_excluded(ranked, plan)
        ranked = self._filter_demo_sources(ranked)
        return [
            SearchHit(
                **{
                    **hit,
                    "score": round(score, 4),
                    "relevance_note": self._relevance_note(hit, plan, profile),
                }
            )
            for hit, score in ranked[:limit]
        ]

    def _vector_candidates(self, conn, plan: QueryPlan, profile: UserProfile, limit: int) -> list[tuple[dict, float]]:
        query_text = " ".join(self._query_candidates(plan)[:8])
        query_vector = embed_text(query_text)
        rows = conn.execute(
            """
            SELECT d.*,
                   c.chunk_text AS chunk_text,
                   c.heading AS heading,
                   c.page AS page,
                   c.attachment_name AS attachment_name,
                   c.chunk_kind AS chunk_kind,
                   c.tags_json AS tags_json,
                   c.embedding_json AS embedding_json
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.embedding_json IS NOT NULL
            ORDER BY c.id DESC
            LIMIT ?
            """
            ,
            (VECTOR_SCAN_LIMIT,),
        ).fetchall()
        candidates: list[tuple[dict, float]] = []
        for row in rows:
            if not embedding_json_matches_current(row["embedding_json"]):
                continue
            similarity = cosine_similarity(query_vector, vector_from_json(row["embedding_json"]))
            if similarity <= VECTOR_MIN_SIMILARITY:
                continue
            hit_dict = row_to_hit(row, similarity, row["chunk_text"][:260])
            score = self._score(hit_dict, plan, profile, self._vector_base_score(similarity, hit_dict, plan))
            candidates.append((hit_dict, score))
        return sorted(candidates, key=lambda item: item[1], reverse=True)[:limit]

    @staticmethod
    def _should_use_vector_recall(plan: QueryPlan) -> bool:
        if plan.intent in {"latest_updates", "unknown"}:
            return False
        return True

    @staticmethod
    def _vector_base_score(similarity: float, hit: dict, plan: QueryPlan) -> float:
        score = similarity * 4.0
        if hit.get("chunk_kind") == "attachment_text":
            score -= 0.35
        if plan.intent == "attachment_query" and hit.get("attachments"):
            score += 0.25
        return max(0.1, score)

    def _profile_candidates(self, conn, plan: QueryPlan, profile: UserProfile, limit: int) -> list[tuple[dict, float]]:
        terms = self._profile_terms(plan, profile)
        if not terms:
            return []
        rows_by_id: dict[int, tuple[dict, float]] = {}
        for term in terms[:10]:
            rows = conn.execute(
                """
                SELECT *
                FROM documents
                WHERE source LIKE ?
                   OR title LIKE ?
                   OR body LIKE ?
                   OR applicable_colleges_json LIKE ?
                   OR applicable_grades_json LIKE ?
                   OR student_types_json LIKE ?
                ORDER BY publish_date DESC, id DESC
                LIMIT 30
                """,
                (f"%{term}%", f"%{term}%", f"%{term}%", f"%{term}%", f"%{term}%", f"%{term}%"),
            ).fetchall()
            for row in rows:
                hit_dict = row_to_hit(row, 0.7, self._make_snippet(row["body"], term))
                score = self._score(hit_dict, plan, profile, 0.7)
                old = rows_by_id.get(hit_dict["id"])
                if old is None or score > old[1]:
                    rows_by_id[hit_dict["id"]] = (hit_dict, score)
        return sorted(rows_by_id.values(), key=lambda item: item[1], reverse=True)[:limit]

    @staticmethod
    def _profile_terms(plan: QueryPlan, profile: UserProfile) -> list[str]:
        values: list[str] = []
        for key in ("college", "grade", "student_type"):
            value = plan.filters.get(key) or getattr(profile, key, None)
            if isinstance(value, str):
                values.append(value)
        if plan.authority_preference:
            values.append(plan.authority_preference)
        expanded: list[str] = []
        for value in values:
            expanded.append(value)
            if value.endswith("学院") and len(value) > 4:
                expanded.append(value.replace("科学与工程", "").replace("学院", "学院"))
                expanded.append(value[:4])
            if value == "大二":
                expanded.extend(["2024级", "二年级", "大二下"])
        deduped: list[str] = []
        seen: set[str] = set()
        for term in expanded:
            term = term.strip()
            if term and term not in seen:
                seen.add(term)
                deduped.append(term)
        return deduped

    @staticmethod
    def _dedupe_ranked(ranked: list[tuple[dict, float]]) -> list[tuple[dict, float]]:
        deduped: list[tuple[dict, float]] = []
        seen: set[str] = set()
        for hit, score in ranked:
            key = re.sub(r"\s+", "", hit.get("title") or "").lower()
            if not key:
                key = re.sub(r"\s+", "", (hit.get("snippet") or "")[:120]).lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append((hit, score))
        return deduped

    @staticmethod
    def _filter_excluded(ranked: list[tuple[dict, float]], plan: QueryPlan) -> list[tuple[dict, float]]:
        exclude_terms = [term for term in plan.exclude_terms if term]
        if not exclude_terms:
            return ranked

        def excluded(hit: dict) -> bool:
            haystack = " ".join(
                [
                    hit.get("title") or "",
                    hit.get("source") or "",
                    hit.get("category") or "",
                    hit.get("snippet") or "",
                    " ".join(hit.get("keywords") or []),
                    " ".join(item.get("name", "") for item in hit.get("attachments") or []),
                ]
            )
            return any(term in haystack for term in exclude_terms)

        filtered = [item for item in ranked if not excluded(item[0])]
        return filtered

    @staticmethod
    def _filter_demo_sources(ranked: list[tuple[dict, float]]) -> list[tuple[dict, float]]:
        return [item for item in ranked if "/demo/" not in (item[0].get("url") or "")]

    @staticmethod
    def _topic_terms(topic: str) -> list[str]:
        terms = [topic, *expand_terms(topic)]
        deduped: list[str] = []
        seen: set[str] = set()
        for term in terms:
            if term and term not in seen:
                seen.add(term)
                deduped.append(term)
        return deduped

    @staticmethod
    def _hit_haystack(hit: dict, include_body: bool) -> str:
        fields = [
            hit.get("title") or "",
            hit.get("source") or "",
            hit.get("category") or "",
            " ".join(hit.get("topics") or []),
            " ".join(hit.get("keywords") or []),
            hit.get("attachment_name") or "",
            " ".join(item.get("name", "") for item in hit.get("attachments") or []),
        ]
        if include_body:
            fields.extend([hit.get("snippet") or "", hit.get("matched_chunk_text") or ""])
        return " ".join(fields)

    @staticmethod
    def _titleish_haystack(hit: dict) -> str:
        return " ".join(
            [
                hit.get("title") or "",
                hit.get("heading") or "",
                hit.get("attachment_name") or "",
                " ".join(item.get("name", "") for item in hit.get("attachments") or []),
            ]
        )

    @staticmethod
    def _metadata_haystack(hit: dict) -> str:
        return " ".join(
            [
                hit.get("source") or "",
                hit.get("category") or "",
                " ".join(hit.get("topics") or []),
                " ".join(hit.get("keywords") or []),
                " ".join(hit.get("chunk_tags") or []),
            ]
        )

    @staticmethod
    def _body_haystack(hit: dict) -> str:
        return " ".join([hit.get("snippet") or "", hit.get("matched_chunk_text") or ""])

    @staticmethod
    def _like_terms(plan: QueryPlan, query_candidates: list[str]) -> list[str]:
        terms: list[str] = []
        for query in query_candidates:
            if query:
                terms.append(query.strip())
            terms.extend(TOKEN_RE.findall(query or ""))
        topic = plan.entities.get("topic")
        if isinstance(topic, str):
            terms.append(topic)
        deduped: list[str] = []
        seen: set[str] = set()
        for term in terms:
            term = term.strip()
            if len(term) < 2 or term.lower() in seen:
                continue
            seen.add(term.lower())
            deduped.append(term)
        return deduped

    @staticmethod
    def _query_candidates(plan: QueryPlan) -> list[str]:
        terms = [
            plan.normalized_query,
            *plan.sub_questions,
            *plan.retrieval_keywords,
            *plan.expanded_queries,
        ]
        if not any(term for term in terms):
            terms = expand_terms(plan.normalized_query)
        return SearchEngine._dedupe_terms(terms)

    @staticmethod
    def _plan_match_terms(plan: QueryPlan, limit: int = 12) -> list[str]:
        return SearchEngine._dedupe_terms(
            [plan.normalized_query, *plan.sub_questions, *plan.retrieval_keywords, *plan.expanded_queries]
        )[:limit]

    @staticmethod
    def _dedupe_terms(terms: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for term in terms:
            term = re.sub(r"\s+", " ", str(term or "")).strip()
            key = term.lower()
            if len(term) < 2 or key in seen:
                continue
            seen.add(key)
            output.append(term)
        return output

    @staticmethod
    def _make_snippet(body: str, term: str) -> str:
        if not body:
            return ""
        index = body.find(term)
        if index < 0:
            return body[:180]
        start = max(0, index - 80)
        end = min(len(body), index + len(term) + 100)
        snippet = body[start:end]
        return snippet.replace(term, f"<mark>{term}</mark>")

    def _score(self, hit: dict, plan: QueryPlan, profile: UserProfile, base_score: float) -> float:
        score = base_score
        title = hit["title"] or ""
        normalized_query = plan.normalized_query
        titleish_haystack = self._titleish_haystack(hit)
        metadata_haystack = self._metadata_haystack(hit)
        body_haystack = self._body_haystack(hit)
        haystack_without_body = f"{titleish_haystack} {metadata_haystack}"
        haystack_with_body = f"{haystack_without_body} {body_haystack}"
        specific_terms = self._specific_match_terms(plan)
        if normalized_query and normalized_query in title:
            score += 5.0
        if specific_terms:
            title_hits = [term for term in specific_terms if term in titleish_haystack]
            body_hits = [term for term in specific_terms if term in body_haystack]
            metadata_hits = [
                term
                for term in specific_terms
                if term not in title_hits and term not in body_hits and term in metadata_haystack
            ]
            score += min(5.0, len(title_hits) * 1.8)
            score += min(2.4, len(body_hits) * 0.6)
            score += min(1.2, len(metadata_hits) * 0.35)
            if plan.intent in {"find_document", "attachment_query"}:
                if title_hits:
                    score += min(4.0, len(title_hits) * 1.5)
                elif body_hits:
                    score -= 5.0
                else:
                    score -= 6.0
            elif plan.intent in {"deadline_query", "process_guide", "eligibility_query"} and not (title_hits or body_hits):
                score -= 1.5
        for query in self._plan_match_terms(plan, 8):
            if query and query in title:
                score += 0.6 if self._is_generic_match_term(query) else 2.0
            if query and query in hit.get("keywords", []):
                score += 0.3 if self._is_generic_match_term(query) else 1.4
        topic = plan.entities.get("topic")
        if isinstance(topic, str) and topic:
            topic_terms = self._topic_terms(topic)
            if any(term and term in titleish_haystack for term in topic_terms):
                score += 3.0
            elif any(term and term in body_haystack for term in topic_terms):
                score += 1.2
            elif any(term and term in metadata_haystack for term in topic_terms):
                score += 0.5
            elif plan.intent in {"deadline_query", "process_guide", "eligibility_query"}:
                score -= 2.5
        action = plan.entities.get("action")
        if isinstance(action, str) and action:
            if action in title:
                score += 0.4 if self._is_generic_match_term(action) else 1.2
            elif action in haystack_with_body:
                score += 0.1 if self._is_generic_match_term(action) else 0.4

        if hit["source"] == "教务处":
            score += 0.9
        source_profile = self.source_profiles.get(hit["source"] or "")
        if source_profile:
            score += min(2.5, float(source_profile.get("authority_weight") or 1.0) * 0.35)
        if plan.authority_preference and plan.authority_preference in (hit["source"] or ""):
            score += 2.2
        if plan.intent == "attachment_query" and hit["attachments"]:
            score += 2.0
        if plan.intent == "latest_updates":
            score += _recency_bonus(hit["publish_date"]) * 2
            if re.search(r"(通知|公告|公示|安排|查询)", title):
                score += 2.2
            else:
                score -= 1.2
        else:
            score += _recency_bonus(hit["publish_date"])
        if plan.intent == "deadline_query":
            score += _deadline_bonus(hit["deadline"])
        score += self._time_scope_bonus(hit, plan)
        score += self._time_scope_penalty(hit, plan)

        if _contains_any(hit["applicable_colleges"], profile.college):
            score += 2.5
        if _contains_any(hit["applicable_grades"], profile.grade):
            score += 1.8
        if _contains_any(hit["student_types"], profile.student_type):
            score += 1.2

        filter_college = plan.filters.get("college")
        if isinstance(filter_college, str) and _contains_any(hit["applicable_colleges"], filter_college):
            score += 2.0
        filter_grade = plan.filters.get("grade")
        if isinstance(filter_grade, str) and _contains_any(hit["applicable_grades"], filter_grade):
            score += 1.5
        filter_student_type = plan.filters.get("student_type")
        if isinstance(filter_student_type, str):
            if _contains_any(hit["student_types"], filter_student_type):
                score += 1.5
            elif hit["student_types"] and not _contains_any(hit["student_types"], filter_student_type):
                score -= 3.0
        return score

    @staticmethod
    def _specific_match_terms(plan: QueryPlan) -> list[str]:
        values: list[str] = []
        for term in SearchEngine._plan_match_terms(plan, 16):
            compact = re.sub(r"\s+", "", str(term or ""))
            if compact and not SearchEngine._is_generic_match_term(compact):
                values.append(compact)
        topic = plan.entities.get("topic")
        if isinstance(topic, str) and not SearchEngine._is_generic_match_term(topic):
            values.append(topic)
        for key in ("college", "grade", "student_type"):
            value = plan.filters.get(key) or plan.entities.get(key)
            if isinstance(value, str) and not SearchEngine._is_generic_match_term(value):
                values.append(value)
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            if len(value) < 2 or value.lower() in seen:
                continue
            seen.add(value.lower())
            deduped.append(value)
        return deduped[:10]

    @staticmethod
    def _is_generic_match_term(term: str | None) -> bool:
        if not term:
            return True
        compact = re.sub(r"\s+", "", str(term))
        if compact in GENERIC_MATCH_TERMS:
            return True
        if re.fullmatch(r"20\d{2}", compact):
            return True
        return len(compact) < 2

    @staticmethod
    def _time_scope_bonus(hit: dict, plan: QueryPlan) -> float:
        scope = plan.time_scope
        publish_date = hit.get("publish_date")
        title = hit.get("title") or ""
        if not scope:
            return 0
        if scope.isdigit():
            bonus = 1.5 if scope in title else 0
            if publish_date and publish_date.startswith(scope):
                bonus += 1.2
            return bonus
        if scope == "current":
            age_days = _publish_age_days(publish_date)
            if age_days is not None and age_days <= CURRENT_SCOPE_DAYS:
                return 2.4 + _recency_bonus(publish_date) * 1.8
            return _recency_bonus(publish_date) * 0.6
        if scope == "recent_2y":
            age_days = _publish_age_days(publish_date)
            if age_days is not None and age_days <= CURRENT_SCOPE_DAYS:
                return 1.6 + _recency_bonus(publish_date)
            return 0
        if scope == "historical":
            return -_recency_bonus(publish_date)
        return 0

    @staticmethod
    def _time_scope_penalty(hit: dict, plan: QueryPlan) -> float:
        if not _time_scope_is_currentish(plan.time_scope):
            return 0
        age_days = _publish_age_days(hit.get("publish_date"))
        if age_days is None:
            return 0
        if age_days > VERY_OLD_SCOPE_DAYS:
            return -8.0
        if age_days > CURRENT_SCOPE_DAYS:
            return -3.5
        return 0

    @staticmethod
    def _relevance_note(hit: dict, plan: QueryPlan, profile: UserProfile) -> str:
        reasons: list[str] = []
        title = hit["title"] or ""
        body = hit["snippet"] or ""
        for query in SearchEngine._plan_match_terms(plan, 8):
            if query and query in title:
                reasons.append(f"标题包含“{query}”")
                break
        for query in SearchEngine._plan_match_terms(plan, 8):
            if query and query in body:
                reasons.append(f"正文片段包含“{query}”")
                break
        if plan.intent == "deadline_query":
            if hit.get("deadline"):
                reasons.append(f"提取到截止日期 {hit['deadline']}")
            elif hit.get("publish_date"):
                reasons.append(f"发布时间为 {hit['publish_date']}")
        if plan.intent == "attachment_query" and hit.get("attachments"):
            reasons.append("包含附件/下载材料")
        topic = plan.entities.get("topic")
        if isinstance(topic, str) and topic:
            topic_terms = SearchEngine._topic_terms(topic)
            haystack = " ".join(
                [
                    title,
                    body,
                    " ".join(hit.get("topics", [])),
                    " ".join(hit.get("keywords", [])),
                ]
            )
            if any(term and term in haystack for term in topic_terms):
                reasons.append(f"主题匹配：{topic}")
        action = plan.entities.get("action")
        if isinstance(action, str) and action and (
            action in title or action in body or action in " ".join(hit.get("keywords", []))
        ):
            reasons.append(f"匹配事项：{action}")
        keyword_matches = []
        for query in SearchEngine._plan_match_terms(plan, 12):
            for keyword in hit.get("keywords", []):
                if query and keyword and (query in keyword or keyword in query):
                    keyword_matches.append(keyword)
        if keyword_matches:
            deduped_keywords = list(dict.fromkeys(keyword_matches))
            reasons.append("关键词匹配：" + "、".join(deduped_keywords[:3]))
        if _contains_any(hit.get("applicable_colleges", []), profile.college):
            reasons.append(f"匹配学院：{profile.college}")
        if _contains_any(hit.get("applicable_grades", []), profile.grade):
            reasons.append(f"匹配年级：{profile.grade}")
        if _contains_any(hit.get("student_types", []), profile.student_type):
            reasons.append(f"匹配身份：{profile.student_type}")
        if hit.get("source") == "教务处":
            reasons.append("教务处来源")
        if plan.authority_preference and plan.authority_preference in (hit.get("source") or ""):
            reasons.append(f"权威来源偏好：{plan.authority_preference}")
        if plan.time_scope:
            reasons.append(f"时间范围：{plan.time_scope}")
        if not reasons and hit.get("keywords"):
            reasons.append("关键词：" + "、".join(hit["keywords"][:3]))
        return "；".join(reasons[:4])
