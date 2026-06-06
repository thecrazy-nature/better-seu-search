from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any
from urllib.parse import urlparse

from backend.app.content_cleaning import boilerplate_score as content_boilerplate_score
from backend.app.config import settings
from backend.app.embeddings import embedding_json_matches_current, embedding_provider


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "tests" / "outputs"
DEFAULT_REPORT = OUTPUT_DIR / "database_quality_report.md"
DEFAULT_JSON = OUTPUT_DIR / "database_quality_report.json"
URL_DATE_RE = re.compile(r"/(20\d{2})/(\d{4})/")


def json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def pct(part: int | float, total: int | float) -> float:
    return round(float(part) / float(total) * 100, 1) if total else 0.0


def q(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    index = min(len(values) - 1, max(0, math.ceil(len(values) * quantile) - 1))
    return int(values[index])


def short(text: str | None, limit: int = 72) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned[: limit - 1] + "…" if len(cleaned) > limit else cleaned


def domain(url: str | None) -> str:
    if not url:
        return ""
    return urlparse(url).netloc.lower()


def audit(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    docs = conn.execute("SELECT * FROM documents").fetchall()
    chunks = conn.execute("SELECT * FROM document_chunks").fetchall()
    source_rows = conn.execute(
        """
        SELECT source,
               COUNT(*) AS count,
               MIN(publish_date) AS oldest,
               MAX(publish_date) AS latest,
               SUM(CASE WHEN publish_date IS NULL OR publish_date = '' THEN 1 ELSE 0 END) AS missing_dates
        FROM documents
        GROUP BY source
        ORDER BY count DESC, latest DESC
        """
    ).fetchall()
    docs_per_chunk = conn.execute(
        """
        SELECT document_id, COUNT(*) AS n
        FROM document_chunks
        GROUP BY document_id
        """
    ).fetchall()
    conn.close()

    today = date.today()
    document_count = len(docs)
    chunk_count = len(chunks)
    body_lengths = [len(row["body"] or "") for row in docs]
    short_docs = [row for row in docs if len(row["body"] or "") < 120]
    very_short_docs = [row for row in docs if len(row["body"] or "") < 40]
    missing_dates = [row for row in docs if not parse_date(row["publish_date"])]
    old_2y = [row for row in docs if (d := parse_date(row["publish_date"])) and (today - d).days > 730]
    old_4y = [row for row in docs if (d := parse_date(row["publish_date"])) and (today - d).days > 1460]
    future_dates = [row for row in docs if (d := parse_date(row["publish_date"])) and d > today]

    title_counter = Counter(re.sub(r"\s+", "", row["title"] or "") for row in docs)
    duplicate_titles = [title for title, count in title_counter.items() if title and count > 1]
    body_counter = Counter((row["body"] or "")[:1200] for row in docs if row["body"])
    duplicate_body_prefixes = [body for body, count in body_counter.items() if body and count > 1]

    url_year_mismatches = []
    demo_urls = []
    non_seu_urls = []
    for row in docs:
        url = row["url"] or ""
        if "/demo/" in url:
            demo_urls.append(row)
        host = domain(url)
        if host and not host.endswith("seu.edu.cn"):
            non_seu_urls.append(row)
        match = URL_DATE_RE.search(url)
        published = parse_date(row["publish_date"])
        if match and published and match.group(1) != str(published.year):
            url_year_mismatches.append(row)

    all_attachments = []
    docs_with_attachments = 0
    for row in docs:
        attachments = json_list(row["attachments_json"])
        if attachments:
            docs_with_attachments += 1
        all_attachments.extend(attachments)
    attachments_with_text = [item for item in all_attachments if item.get("text")]
    attachment_ext_counts = Counter(_attachment_ext(item) for item in all_attachments)
    attachment_missing_text_by_ext = Counter(
        _attachment_ext(item) for item in all_attachments if not item.get("text")
    )

    chunk_kinds = Counter(row["chunk_kind"] or "unknown" for row in chunks)
    chunks_per_doc = [int(row["n"]) for row in docs_per_chunk]
    docs_without_chunks = document_count - len(docs_per_chunk)
    empty_search_text = [row for row in chunks if not (row["search_text"] or "").strip()]
    zero_token_chunks = [row for row in chunks if int(row["token_count"] or 0) == 0]
    short_chunks = [row for row in chunks if len(row["chunk_text"] or "") < 20]
    attachment_text_chunks = [row for row in chunks if row["chunk_kind"] == "attachment_text"]
    missing_embeddings = [row for row in chunks if not row["embedding_json"]]
    stale_embeddings = [
        row
        for row in chunks
        if row["embedding_json"] and not embedding_json_matches_current(row["embedding_json"])
    ]
    chunk_tag_lists = [json_list(row_value(row, "tags_json")) for row in chunks]
    chunk_tag_counts = [len(tags) for tags in chunk_tag_lists]
    chunks_without_tags = [row for row, tags in zip(chunks, chunk_tag_lists) if not tags]
    top_chunk_tags = Counter(tag for tags in chunk_tag_lists for tag in tags)

    topic_empty = [row for row in docs if not json_list(row["topics_json"])]
    keyword_empty = [row for row in docs if not json_list(row["keywords_json"])]
    ai_metadata_docs = [row for row in docs if json_dict(row_value(row, "ai_metadata_json"))]
    ai_metadata_topics = Counter(
        topic
        for row in docs
        for topic in _metadata_list(row, "topics")
    )
    audience_any = [
        row
        for row in docs
        if json_list(row["applicable_colleges_json"])
        or json_list(row["applicable_grades_json"])
        or json_list(row["student_types_json"])
    ]
    topic_counts = Counter(topic for row in docs for topic in json_list(row["topics_json"]))
    keyword_counts = Counter(keyword for row in docs for keyword in json_list(row["keywords_json"]))

    body_boilerplate = []
    for row in docs:
        body = row["body"] or ""
        if len(body) >= 120 and _boilerplate_score(body) >= 3:
            body_boilerplate.append(row)

    metrics: dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "db_path": str(db_path),
        "embedding_provider": embedding_provider(),
        "documents": document_count,
        "chunks": chunk_count,
        "docs_with_attachments": docs_with_attachments,
        "attachments": len(all_attachments),
        "attachments_with_text": len(attachments_with_text),
        "attachment_text_chunks": len(attachment_text_chunks),
        "missing_publish_date": len(missing_dates),
        "future_publish_date": len(future_dates),
        "old_over_2y": len(old_2y),
        "old_over_4y": len(old_4y),
        "duplicate_title_count": len(duplicate_titles),
        "duplicate_body_prefix_count": len(duplicate_body_prefixes),
        "demo_url_count": len(demo_urls),
        "non_seu_url_count": len(non_seu_urls),
        "url_year_mismatch_count": len(url_year_mismatches),
        "short_body_count": len(short_docs),
        "very_short_body_count": len(very_short_docs),
        "body_boilerplate_suspect_count": len(body_boilerplate),
        "docs_without_chunks": docs_without_chunks,
        "empty_search_text_chunks": len(empty_search_text),
        "zero_token_chunks": len(zero_token_chunks),
        "short_chunk_count": len(short_chunks),
        "missing_embedding_chunks": len(missing_embeddings),
        "stale_embedding_chunks": len(stale_embeddings),
        "chunks_without_tags": len(chunks_without_tags),
        "topic_empty_docs": len(topic_empty),
        "keyword_empty_docs": len(keyword_empty),
        "ai_metadata_docs": len(ai_metadata_docs),
        "audience_tagged_docs": len(audience_any),
        "body_length": {
            "min": min(body_lengths) if body_lengths else 0,
            "median": int(median(body_lengths)) if body_lengths else 0,
            "mean": int(mean(body_lengths)) if body_lengths else 0,
            "max": max(body_lengths) if body_lengths else 0,
        },
        "chunks_per_doc": {
            "min": min(chunks_per_doc) if chunks_per_doc else 0,
            "median": int(median(chunks_per_doc)) if chunks_per_doc else 0,
            "mean": round(mean(chunks_per_doc), 1) if chunks_per_doc else 0,
            "p95": q(chunks_per_doc, 0.95),
            "max": max(chunks_per_doc) if chunks_per_doc else 0,
        },
        "chunk_tags_per_chunk": {
            "min": min(chunk_tag_counts) if chunk_tag_counts else 0,
            "median": int(median(chunk_tag_counts)) if chunk_tag_counts else 0,
            "mean": round(mean(chunk_tag_counts), 1) if chunk_tag_counts else 0,
            "p95": q(chunk_tag_counts, 0.95),
            "max": max(chunk_tag_counts) if chunk_tag_counts else 0,
        },
        "chunk_kinds": dict(chunk_kinds.most_common()),
        "attachment_ext_counts": dict(attachment_ext_counts.most_common()),
        "attachment_missing_text_by_ext": dict(attachment_missing_text_by_ext.most_common()),
        "top_sources": [dict(row) for row in source_rows[:15]],
        "top_topics": topic_counts.most_common(20),
        "top_ai_metadata_topics": ai_metadata_topics.most_common(20),
        "top_keywords": keyword_counts.most_common(20),
        "top_chunk_tags": top_chunk_tags.most_common(30),
        "samples": {
            "missing_dates": [_doc_sample(row) for row in missing_dates[:8]],
            "url_year_mismatches": [_doc_sample(row) for row in url_year_mismatches[:8]],
            "short_docs": [_doc_sample(row) for row in short_docs[:8]],
            "body_boilerplate": [_doc_sample(row) for row in body_boilerplate[:8]],
            "duplicate_titles": duplicate_titles[:8],
            "demo_urls": [_doc_sample(row) for row in demo_urls[:8]],
            "non_seu_urls": [_doc_sample(row) for row in non_seu_urls[:8]],
        },
    }
    metrics["verdict"] = _verdict(metrics)
    metrics["strengths"] = _strengths(metrics)
    metrics["risks"] = _risks(metrics)
    return metrics


def render_report(metrics: dict[str, Any]) -> str:
    lines = [
        "# 数据库质量审计报告",
        "",
        "## 总体结论",
        "",
        f"- 结论：{metrics['verdict']}",
        f"- 审计时间：{metrics['created_at']}",
        f"- 数据库：`{metrics['db_path']}`",
        f"- Embedding：`{metrics['embedding_provider']}`",
        "",
        "## 核心指标",
        "",
        f"- 文档数：{metrics['documents']}",
        f"- Chunk 数：{metrics['chunks']}，每篇文档 chunk 中位数 {metrics['chunks_per_doc']['median']}，P95 {metrics['chunks_per_doc']['p95']}，最大 {metrics['chunks_per_doc']['max']}",
        f"- 附件数：{metrics['attachments']}，已解析正文 {metrics['attachments_with_text']}（{pct(metrics['attachments_with_text'], metrics['attachments'])}%）",
        f"- 附件正文 chunk：{metrics['attachment_text_chunks']}（占全部 chunk {pct(metrics['attachment_text_chunks'], metrics['chunks'])}%）",
        f"- 缺发布时间：{metrics['missing_publish_date']}（{pct(metrics['missing_publish_date'], metrics['documents'])}%）",
        f"- 两年以上旧文档：{metrics['old_over_2y']}（{pct(metrics['old_over_2y'], metrics['documents'])}%）；四年以上旧文档：{metrics['old_over_4y']}（{pct(metrics['old_over_4y'], metrics['documents'])}%）",
        f"- 正文过短文档（<120 字）：{metrics['short_body_count']}（{pct(metrics['short_body_count'], metrics['documents'])}%）",
        f"- 正文疑似模板污染：{metrics['body_boilerplate_suspect_count']}（{pct(metrics['body_boilerplate_suspect_count'], metrics['documents'])}%）",
        f"- 主题标签缺失：{metrics['topic_empty_docs']}（{pct(metrics['topic_empty_docs'], metrics['documents'])}%）；关键词缺失：{metrics['keyword_empty_docs']}（{pct(metrics['keyword_empty_docs'], metrics['documents'])}%）",
        f"- AI 元数据覆盖：{metrics['ai_metadata_docs']}（{pct(metrics['ai_metadata_docs'], metrics['documents'])}%）",
        f"- Chunk 标签缺失：{metrics['chunks_without_tags']}（{pct(metrics['chunks_without_tags'], metrics['chunks'])}%）；每个 chunk 标签数中位数 {metrics['chunk_tags_per_chunk']['median']}，平均 {metrics['chunk_tags_per_chunk']['mean']}，P95 {metrics['chunk_tags_per_chunk']['p95']}",
        f"- 有适用对象标签的文档：{metrics['audience_tagged_docs']}（{pct(metrics['audience_tagged_docs'], metrics['documents'])}%）",
        f"- 缺 embedding chunk：{metrics['missing_embedding_chunks']}；过期 embedding chunk：{metrics['stale_embedding_chunks']}",
        f"- Demo URL：{metrics['demo_url_count']}；非 seu.edu.cn URL：{metrics['non_seu_url_count']}；URL 年份与发布时间不一致：{metrics['url_year_mismatch_count']}",
        "",
        "## 强项",
        "",
    ]
    lines.extend(f"- {item}" for item in metrics["strengths"])
    lines.extend(["", "## 风险点", ""])
    lines.extend(f"- {item}" for item in metrics["risks"])

    lines.extend(["", "## Chunk 类型", ""])
    for kind, count in metrics["chunk_kinds"].items():
        lines.append(f"- {kind}: {count}")

    lines.extend(["", "## 附件类型", ""])
    for ext, count in metrics["attachment_ext_counts"].items():
        missing = metrics["attachment_missing_text_by_ext"].get(ext, 0)
        lines.append(f"- {ext}: {count}，未解析正文 {missing}")

    lines.extend(["", "## 来源分布", ""])
    lines.extend(["| 来源 | 数量 | 最早 | 最新 | 缺发布时间 |", "| --- | ---: | --- | --- | ---: |"])
    for row in metrics["top_sources"]:
        lines.append(
            f"| {row.get('source')} | {row.get('count')} | {row.get('oldest') or '-'} | {row.get('latest') or '-'} | {row.get('missing_dates') or 0} |"
        )

    lines.extend(["", "## 高频主题/关键词", ""])
    topics = "、".join(f"{topic}({count})" for topic, count in metrics["top_topics"][:12]) or "-"
    ai_topics = "、".join(f"{topic}({count})" for topic, count in metrics["top_ai_metadata_topics"][:12]) or "-"
    keywords = "、".join(f"{keyword}({count})" for keyword, count in metrics["top_keywords"][:12]) or "-"
    chunk_tags = "、".join(f"{tag}({count})" for tag, count in metrics["top_chunk_tags"][:16]) or "-"
    lines.append(f"- 主题：{topics}")
    lines.append(f"- AI 元数据主题：{ai_topics}")
    lines.append(f"- 关键词：{keywords}")
    lines.append(f"- Chunk 标签：{chunk_tags}")

    samples = metrics["samples"]
    lines.extend(["", "## 样例检查", ""])
    _append_sample_section(lines, "缺发布时间", samples["missing_dates"])
    _append_sample_section(lines, "URL 年份与发布时间不一致", samples["url_year_mismatches"])
    _append_sample_section(lines, "正文过短", samples["short_docs"])
    _append_sample_section(lines, "正文疑似模板污染", samples["body_boilerplate"])
    if samples["duplicate_titles"]:
        lines.extend(["", "重复标题样例："])
        lines.extend(f"- {title}" for title in samples["duplicate_titles"])
    _append_sample_section(lines, "Demo URL", samples["demo_urls"])
    _append_sample_section(lines, "非 seu.edu.cn URL", samples["non_seu_urls"])

    lines.extend(
        [
            "",
            "## 判断口径",
            "",
            "- `publish_date` 只看数据库字段，不从正文活动时间推断。",
            "- URL 年份一致性只检查 `.../YYYY/MMDD/...` 这类官网常见路径。",
            "- “正文过短”不必然错误，但容易导致 AI 总结时上下文不足。",
            f"- 当前 embedding provider：`{metrics['embedding_provider']}`；BGE/API 适合语义召回，`hash` 只适合作离线兜底。",
        ]
    )
    return "\n".join(lines) + "\n"


def _append_sample_section(lines: list[str], title: str, samples: list[dict[str, Any]]) -> None:
    lines.extend(["", f"{title}样例："])
    if not samples:
        lines.append("- 无")
        return
    for item in samples:
        lines.append(
            f"- #{item['id']} 《{item['title']}》 | {item['source']} | {item['publish_date'] or '-'} | {item['url']}"
        )


def _attachment_ext(item: dict[str, Any]) -> str:
    name = item.get("name") or item.get("url") or ""
    match = re.search(r"\.([A-Za-z0-9]{1,6})(?:$|\?)", name)
    return f".{match.group(1).lower()}" if match else "unknown"


def _doc_sample(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": short(row["title"], 90),
        "source": row["source"],
        "publish_date": row["publish_date"],
        "url": row["url"],
    }


def _metadata_list(row: sqlite3.Row, key: str) -> list[Any]:
    value = json_dict(row_value(row, "ai_metadata_json")).get(key)
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        return [value]
    return []


def _boilerplate_score(body: str) -> int:
    return content_boilerplate_score(body)


def _verdict(metrics: dict[str, Any]) -> str:
    score = 0
    if metrics["documents"] >= 400 and metrics["chunks"] >= metrics["documents"] * 3:
        score += 2
    if pct(metrics["missing_publish_date"], metrics["documents"]) <= 3:
        score += 2
    if pct(metrics["attachments_with_text"], metrics["attachments"]) >= 80:
        score += 2
    if metrics["empty_search_text_chunks"] == 0 and metrics["zero_token_chunks"] == 0:
        score += 2
    if metrics.get("chunks_without_tags", 0) == 0:
        score += 1
    if pct(metrics["topic_empty_docs"], metrics["documents"]) <= 25:
        score += 1
    if pct(metrics["audience_tagged_docs"], metrics["documents"]) >= 25:
        score += 1
    if metrics["demo_url_count"] == 0:
        score += 1
    if metrics["embedding_provider"] != "hash":
        score += 1

    if score >= 10:
        if metrics["embedding_provider"] == "hash":
            return "结构质量较高，已经适合做 RAG 检索；主要短板在语义 embedding 和标签精度。"
        return "结构质量较高，已经适合做 RAG 检索；主要短板在标签精度、附件噪声和旧文档治理。"
    if score >= 7:
        return "结构质量中上，可用于当前系统；但标签、附件权重和语义向量仍会影响准确率。"
    if score >= 4:
        return "结构质量中等，能跑通检索，但需要优先补日期、正文、标签或附件解析质量。"
    return "结构质量偏弱，当前更像演示索引，建议先补数据清洗和元数据。"


def _strengths(metrics: dict[str, Any]) -> list[str]:
    strengths = []
    if metrics["documents"] >= 400:
        strengths.append("索引规模不是空壳，已覆盖数百篇官网文档。")
    if metrics["chunks"] >= metrics["documents"] * 3:
        strengths.append("文档已拆成标题、正文、附件正文等多类 chunk，本地召回基础较完整。")
    if pct(metrics["attachments_with_text"], metrics["attachments"]) >= 75:
        strengths.append("大部分附件已经解析出正文，能回答只藏在 PDF/DOCX/XLSX 里的问题。")
    if metrics["empty_search_text_chunks"] == 0:
        strengths.append("chunk 检索文本没有空值，标题/栏目/主题/附件名等上下文已进入 FTS。")
    if metrics.get("chunks_without_tags", 0) == 0:
        strengths.append("所有 chunk 都已生成标签，Evidence Judge 和 embedding 都能拿到更完整的上下文。")
    if pct(metrics.get("ai_metadata_docs", 0), metrics["documents"]) >= 50:
        strengths.append("多数文章已有 AI 元数据，检索和证据阅读可以利用离线读文结果辅助定位答案点。")
    if metrics["demo_url_count"] == 0:
        strengths.append("未发现 `/demo/` 演示 URL 混入真实索引。")
    if not strengths:
        strengths.append("基础表结构完整，但需要先修复下方风险点。")
    return strengths


def _risks(metrics: dict[str, Any]) -> list[str]:
    risks = []
    if metrics["embedding_provider"] == "hash":
        risks.append("当前 embedding provider 是 `hash`，只能做词面近似；同义词/口语问题仍主要依赖 Query Planner 和 FTS，建议后续换 BGE 或 API embedding。")
    if metrics.get("chunks_without_tags", 0):
        risks.append(f"仍有 {metrics['chunks_without_tags']} 个 chunk 没有标签，需要先运行 metadata 回填，否则 Evidence Judge 看到的上下文不完整。")
    if metrics.get("stale_embedding_chunks", 0):
        risks.append(f"仍有 {metrics['stale_embedding_chunks']} 个 chunk 的 embedding 过期，需要重新回填，才能让向量检索使用新的标签上下文。")
    if pct(metrics.get("ai_metadata_docs", 0), metrics["documents"]) < 50:
        risks.append("AI 元数据覆盖不足；建议分批运行 `python -X utf8 -m backend.app.backfill_ai_metadata --missing-only --limit 50`，让 AI 离线读文章补主题、动作、可回答问题和附件摘要。")
    attachment_ratio = pct(metrics["attachment_text_chunks"], metrics["chunks"])
    if attachment_ratio >= 40:
        risks.append(f"附件正文 chunk 占比 {attachment_ratio}%，容易让表格/名单/联系方式类附件在候选里过度曝光，需要 Evidence Judge 或权重控制过滤弱证据。")
    if pct(metrics["audience_tagged_docs"], metrics["documents"]) < 30:
        risks.append("学院、年级、学生类型等适用对象标签覆盖不足，不能把这类字段当硬过滤，只适合软加分或交给 AI 判断。")
    if metrics["url_year_mismatch_count"]:
        risks.append("存在 URL 年份与 publish_date 不一致的样例，需要核对爬虫发布日期抽取是否误把正文活动时间当发布时间。")
    if metrics["short_body_count"]:
        risks.append("部分文档正文很短，可能是列表页、附件型通知或正文抽取不完整，AI 总结时上下文会不足。")
    if metrics.get("body_boilerplate_suspect_count", 0):
        risks.append(f"仍有 {metrics['body_boilerplate_suspect_count']} 篇文档正文疑似包含导航、页脚或联系方式模板，建议重新运行正文清洗修复脚本。")
    if metrics["old_over_2y"]:
        risks.append("库中仍有两年以上旧文档；用户问“最近/现在”时必须保留时间过滤和 AI stale 判断。")
    if metrics["missing_publish_date"]:
        risks.append("少量文档缺发布时间；涉及“最近/当前”时这些文档只能低优先级处理。")
    if not risks:
        risks.append("未发现明显结构性风险；后续重点应转向答案质量和召回覆盖率评测。")
    return risks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()

    metrics = audit(args.db)
    report = render_report(metrics)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    args.json_output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print({"report": str(args.output), "json": str(args.json_output), "verdict": metrics["verdict"]})


if __name__ == "__main__":
    main()
