from __future__ import annotations


# Keep this table tiny: only stable aliases/abbreviations that are unlikely to
# drift by context. Business-topic paraphrases should come from AI Planner and
# BGE/vector recall, not from an ever-growing hand-written dictionary.
SYNONYMS: dict[str, list[str]] = {
    "四六级": ["CET", "大学英语四级", "大学英语六级", "全国大学英语四、六级考试", "英语四六级"],
    "CET": ["四六级", "大学英语四级", "大学英语六级", "全国大学英语四、六级考试"],
    "四级": ["CET4", "CET-4", "大学英语四级", "全国大学英语四级考试"],
    "CET4": ["四级", "大学英语四级", "全国大学英语四级考试"],
    "六级": ["CET6", "CET-6", "大学英语六级", "全国大学英语六级考试"],
    "CET6": ["六级", "大学英语六级", "全国大学英语六级考试"],
    "保研": ["推免", "推荐免试", "推荐免试研究生"],
    "推免": ["保研", "推荐免试", "推荐免试研究生"],
}


def expand_terms(query: str) -> list[str]:
    expanded = [query]
    query_lower = query.lower()
    matched_keys = [key for key in SYNONYMS if key.lower() in query_lower]
    specific_keys = [
        key
        for key in matched_keys
        if not any(key != other and key.lower() in other.lower() for other in matched_keys)
    ]
    for key in specific_keys:
        values = SYNONYMS[key]
        expanded.extend(values)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in expanded:
        normalized = item.strip()
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            deduped.append(normalized)
    return deduped
