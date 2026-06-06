from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date
from typing import Any

from .ai.client import make_ai_client
from .config import settings
from .storage import DocumentStore


AI_METADATA_SYSTEM = """
你是东南大学官网检索系统的离线 Document Metadata Reader。
你的任务是阅读一篇官网文章及其附件信息，生成用于检索和证据导航的结构化元数据；不要写面向用户的最终答案。

只使用输入中的 document。不要补充外部事实，不要编造 URL，不要修改 publish_date。
这些元数据用于提高召回和帮助 Evidence Reader 定位答案点；它们不是最终事实依据。

输出 JSON schema:
{
  "topics": ["2-8 个业务主题，如 转专业、四六级、毕业审核"],
  "keywords": ["5-20 个检索关键词，包含官网术语、口语同义词、重要年份/对象/材料名"],
  "business_actions": ["申请|报名|下载|查询|审核|公示|缴费|考试|办理|核对 等"],
  "audience": ["本科生|研究生|2024级|某学院|全校学生 等"],
  "official_terms": ["官网正文里出现或高度对应的正式术语"],
  "answerable_questions": ["这篇文章可以直接回答的自然语言问题"],
  "attachment_summaries": [
    {"name": "附件名", "purpose": "附件用途", "summary": "附件包含的核心信息"}
  ],
  "time_points": [
    {"type": "publish_date|application_time|registration_time|deadline|exam_time|event_time|other", "value": "原文中的时间短语", "meaning": "时间含义"}
  ],
  "confidence": "high|medium|low",
  "notes": "一句话说明元数据质量或风险"
}

规则:
1. topics 和 business_actions 要服务于检索，不要塞泛词，如 通知、工作、学校。
2. answerable_questions 要具体，例如“2026年上半年四六级什么时候报名？”而不是“这是什么通知？”。
3. 附件摘要只概括附件用途和核心内容，不要罗列大量名单。
4. 日期必须标明类型，文章发布时间只能来自 document.publish_date；正文里的活动/报名/考试时间不能当 publish_date。
5. 如果正文很短或附件不可读，confidence 降为 low，并在 notes 说明。
6. 只输出 JSON 对象，不要 Markdown。
"""


MAX_BODY_CHARS = 9000
MAX_ATTACHMENT_TEXT_CHARS = 1800


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0, help="Skip this many selected documents before processing.")
    parser.add_argument("--refresh", action="store_true", help="Regenerate metadata even when ai_metadata_json exists.")
    parser.add_argument("--missing-only", action="store_true", help="Prioritize documents with empty topics or empty AI metadata.")
    parser.add_argument("--dry-run", action="store_true", help="Print generated metadata without writing to the database.")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop the batch on the first AI/API error.")
    parser.add_argument("--retries", type=int, default=2, help="Retry each failed AI request this many times.")
    parser.add_argument("--retry-delay", type=float, default=3.0, help="Base seconds to wait between retries.")
    parser.add_argument("--request-delay", type=float, default=0.0, help="Seconds to wait after each document.")
    parser.add_argument("--timeout", type=float, default=None, help="Override AI request timeout seconds for this batch.")
    args = parser.parse_args()

    store = DocumentStore()
    store.init_db()
    if not settings.ai_api_key:
        raise SystemExit("AI API key is not configured; cannot backfill AI metadata.")
    if args.timeout:
        settings.ai_timeout_seconds = max(1.0, args.timeout)

    rows = _select_documents(
        store,
        limit=args.limit,
        offset=args.offset,
        refresh=args.refresh,
        missing_only=args.missing_only,
    )
    updated = 0
    failed = 0
    for index, row in enumerate(rows, start=1):
        started_at = time.perf_counter()
        print(
            {
                "event": "metadata_start",
                "index": index,
                "total": len(rows),
                "id": row["id"],
                "title": row["title"],
            },
            flush=True,
        )
        try:
            metadata = _read_metadata_with_retries(row, retries=args.retries, retry_delay=args.retry_delay)
        except Exception as exc:
            failed += 1
            print(
                {
                    "error": "metadata_generation_failed",
                    "id": row["id"],
                    "title": row["title"],
                    "message": str(exc)[:300],
                },
                flush=True,
            )
            if args.stop_on_error:
                raise
            continue
        if not metadata:
            failed += 1
            print(
                {
                    "event": "metadata_empty",
                    "index": index,
                    "total": len(rows),
                    "id": row["id"],
                    "title": row["title"],
                },
                flush=True,
            )
            continue
        metadata = _normalize_metadata(metadata)
        if args.dry_run:
            print(json.dumps({"id": row["id"], "title": row["title"], "metadata": metadata}, ensure_ascii=False))
        else:
            store.update_document_ai_metadata(int(row["id"]), metadata)
        updated += 1
        print(
            {
                "event": "metadata_done",
                "index": index,
                "total": len(rows),
                "id": row["id"],
                "topics": metadata.get("topics", [])[:4],
                "confidence": metadata.get("confidence"),
                "elapsed_seconds": round(time.perf_counter() - started_at, 2),
            },
            flush=True,
        )
        if args.request_delay > 0:
            time.sleep(args.request_delay)
    print(
        {
            "selected": len(rows),
            "updated": updated,
            "failed": failed,
            "dry_run": args.dry_run,
            "model": settings.ai_model,
        },
        flush=True,
    )


def _read_metadata_with_retries(
    row: Any,
    retries: int,
    retry_delay: float,
) -> dict[str, Any] | None:
    last_error: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        client = make_ai_client()
        if not client:
            raise RuntimeError("AI API key is not configured.")
        try:
            metadata = _read_metadata(client, row)
            if metadata:
                return metadata
            raise ValueError("AI returned empty or invalid metadata JSON.")
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            wait_seconds = max(0.0, retry_delay) * (attempt + 1)
            print(
                {
                    "warning": "metadata_generation_retry",
                    "id": row["id"],
                    "title": row["title"],
                    "attempt": attempt + 1,
                    "wait_seconds": wait_seconds,
                    "message": str(exc)[:220],
                },
                flush=True,
            )
            if wait_seconds:
                time.sleep(wait_seconds)
        finally:
            _close_client(client)
    if last_error:
        raise last_error
    return None


def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _select_documents(
    store: DocumentStore,
    limit: int | None,
    offset: int,
    refresh: bool,
    missing_only: bool,
) -> list[Any]:
    where = []
    if not refresh:
        where.append("(ai_metadata_json IS NULL OR ai_metadata_json = '' OR ai_metadata_json = '{}')")
    if missing_only:
        where.append("(topics_json IS NULL OR topics_json = '[]' OR ai_metadata_json IS NULL OR ai_metadata_json = '{}' OR ai_metadata_json = '')")
    sql = "SELECT * FROM documents"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY publish_date DESC NULLS LAST, id DESC"
    params: tuple[Any, ...] = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    if offset:
        if not limit:
            sql += " LIMIT -1"
        sql += " OFFSET ?"
        params = (*params, max(0, offset))
    with store.connect() as conn:
        return conn.execute(sql, params).fetchall()


def _read_metadata(client: Any, row: Any) -> dict[str, Any] | None:
    response = client.chat.completions.create(
        model=settings.ai_model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": AI_METADATA_SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_date": date.today().isoformat(),
                        "document": _document_payload(row),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )
    return _parse_json_object(response.choices[0].message.content or "")


def _document_payload(row: Any) -> dict[str, Any]:
    attachments = _json_list(row["attachments_json"])
    return {
        "id": row["id"],
        "title": row["title"],
        "url": row["url"],
        "source": row["source"],
        "category": row["category"],
        "publish_date": row["publish_date"],
        "topics": _json_list(row["topics_json"]),
        "keywords": _json_list(row["keywords_json"]),
        "applicable_colleges": _json_list(row["applicable_colleges_json"]),
        "applicable_grades": _json_list(row["applicable_grades_json"]),
        "student_types": _json_list(row["student_types_json"]),
        "deadline": row["deadline"],
        "body": _clean(row["body"] or "", MAX_BODY_CHARS),
        "attachments": [_attachment_payload(item) for item in attachments[:10] if isinstance(item, dict)],
    }


def _attachment_payload(item: dict[str, Any]) -> dict[str, Any]:
    text = item.get("text") or ""
    if not text and isinstance(item.get("pages"), list):
        text = "\n".join(str(page.get("text") or "") for page in item["pages"][:4] if isinstance(page, dict))
    if not text and isinstance(item.get("sheets"), list):
        text = "\n".join(str(sheet.get("text") or "") for sheet in item["sheets"][:3] if isinstance(sheet, dict))
    return {
        "name": item.get("name"),
        "url": item.get("url"),
        "text_excerpt": _clean(text, MAX_ATTACHMENT_TEXT_CHARS),
    }


def _normalize_metadata(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "topics": _string_list(data.get("topics"), 8),
        "keywords": _string_list(data.get("keywords"), 24),
        "business_actions": _string_list(data.get("business_actions"), 12),
        "audience": _string_list(data.get("audience"), 16),
        "official_terms": _string_list(data.get("official_terms"), 20),
        "answerable_questions": _string_list(data.get("answerable_questions"), 12),
        "attachment_summaries": _attachment_summaries(data.get("attachment_summaries")),
        "time_points": _time_points(data.get("time_points")),
        "confidence": _confidence(data.get("confidence")),
        "notes": _clean(str(data.get("notes") or ""), 260),
        "schema_version": 1,
        "generated_by": settings.ai_model,
        "generated_at": date.today().isoformat(),
    }


def _attachment_summaries(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, str]] = []
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "name": _clean(str(item.get("name") or ""), 120),
                "purpose": _clean(str(item.get("purpose") or ""), 160),
                "summary": _clean(str(item.get("summary") or ""), 260),
            }
        )
    return output


def _time_points(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    allowed = {
        "publish_date",
        "application_time",
        "registration_time",
        "deadline",
        "exam_time",
        "event_time",
        "other",
    }
    output: list[dict[str, str]] = []
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        time_type = str(item.get("type") or "other").strip()
        output.append(
            {
                "type": time_type if time_type in allowed else "other",
                "value": _clean(str(item.get("value") or ""), 120),
                "meaning": _clean(str(item.get("meaning") or ""), 180),
            }
        )
    return output


def _string_list(value: Any, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if len(text) < 2 or text in seen:
            continue
        seen.add(text)
        output.append(text[:120])
        if len(output) >= limit:
            break
    return output


def _confidence(value: Any) -> str:
    text = str(value or "medium").strip().lower()
    return text if text in {"high", "medium", "low"} else "medium"


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


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


def _clean(text: str, max_chars: int) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


if __name__ == "__main__":
    main()
