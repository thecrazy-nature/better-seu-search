from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .config import settings
from .embeddings import (
    embed_text,
    embed_texts,
    embedding_json_matches_current,
    embedding_to_json,
)
from .content_cleaning import clean_body_text
from .models import SourceDocument
from .preprocess import DOMAIN_TERMS, enrich_document
from .attachments import attachment_text_parts
from .search.synonyms import SYNONYMS

CHUNK_TARGET_CHARS = 900
CHUNK_OVERLAP_CHARS = 160
MAX_SEARCH_TEXT_CHARS = 5000
MAX_SEGMENTED_TOKENS = 360
MAX_CHUNK_TAGS = 64
MAX_EMBEDDING_TEXT_CHARS = 2400
DEFAULT_COLLECTION_NAME = "Default Index"
DEFAULT_COLLECTION_SLUG = "default"

try:
    import jieba  # type: ignore
except Exception:  # pragma: no cover - optional acceleration/quality dependency
    jieba = None


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load_list(value: str | None) -> list:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _json_load_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _date_to_str(value: date | None) -> str | None:
    return value.isoformat() if value else None


def make_content_hash(title: str, body: str, url: str, attachments: list[dict[str, Any]] | None = None) -> str:
    attachment_key = json.dumps(attachments or [], ensure_ascii=False, sort_keys=True)
    raw = f"{url}\n{title}\n{body}\n{attachment_key}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()


def _estimate_token_count(text: str) -> int:
    ascii_words = re.findall(r"[A-Za-z0-9_]+", text)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return len(ascii_words) + max(1, len(cjk_chars) // 2)


def _dedupe_terms(values: Iterable[str], limit: int | None = None) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = re.sub(r"\s+", "", str(value or "")).strip()
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        output.append(term)
        if limit and len(output) >= limit:
            break
    return output


def _segment_for_fts(text: str, limit: int = MAX_SEGMENTED_TOKENS) -> str:
    text = re.sub(r"\s+", " ", text or "")[:MAX_SEARCH_TEXT_CHARS]
    if not text:
        return ""
    if jieba is not None:
        return " ".join(_dedupe_terms(jieba.cut_for_search(text), limit=limit))

    tokens: list[str] = []
    tokens.extend(re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|20\d{2}届|20\d{2}级|20\d{2}", text))
    for term in DOMAIN_TERMS:
        if term in text:
            tokens.append(term)
    for topic, aliases in SYNONYMS.items():
        if topic in text:
            tokens.append(topic)
        tokens.extend(alias for alias in aliases if alias in text)
    for run in re.findall(r"[\u4e00-\u9fff]{2,24}", text):
        if len(run) <= 8:
            tokens.append(run)
        for size in (2, 3, 4):
            if len(run) < size:
                continue
            tokens.extend(run[index : index + size] for index in range(0, len(run) - size + 1))
    return " ".join(_dedupe_terms(tokens, limit=limit))


def _build_chunk_search_text(row: sqlite3.Row, chunk: dict[str, object], chunk_text: str) -> str:
    tags = _build_chunk_tags(row, chunk, chunk_text)
    topics = _json_load_list(row["topics_json"])
    keywords = _json_load_list(row["keywords_json"])
    ai_metadata = _json_load_dict(_row_get(row, "ai_metadata_json"))
    attachments = _json_load_list(row["attachments_json"])
    attachment_names = [item.get("name", "") for item in attachments if isinstance(item, dict)]
    metadata_parts = [
        f"标题 {row['title']}",
        f"来源 {row['source']}",
        f"栏目 {row['category'] or ''}",
        f"标题块 {chunk.get('heading') or ''}",
        f"块类型 {chunk.get('chunk_kind') or 'body'}",
        f"主题 {' '.join(str(item) for item in topics)}",
        f"关键词 {' '.join(str(item) for item in keywords)}",
        f"标签 {' '.join(tags)}",
        f"AI元数据 {_ai_metadata_search_text(ai_metadata)}",
        f"附件 {chunk.get('attachment_name') or ''} {' '.join(attachment_names[:8])}",
        f"适用学院 {' '.join(str(item) for item in _json_load_list(row['applicable_colleges_json']))}",
        f"适用年级 {' '.join(str(item) for item in _json_load_list(row['applicable_grades_json']))}",
        f"适用身份 {' '.join(str(item) for item in _json_load_list(row['student_types_json']))}",
        f"正文 {chunk_text}",
    ]
    raw = "\n".join(part for part in metadata_parts if part.strip())
    segmented = _segment_for_fts(raw)
    return f"{raw}\n分词 {segmented}"[:MAX_SEARCH_TEXT_CHARS]


def _build_chunk_tags(row: sqlite3.Row, chunk: dict[str, object], chunk_text: str) -> list[str]:
    title = str(row["title"] or "")
    source = str(row["source"] or "")
    category = str(row["category"] or "")
    heading = str(chunk.get("heading") or "")
    chunk_kind = str(chunk.get("chunk_kind") or "body")
    attachment_name = str(chunk.get("attachment_name") or "")
    text = "\n".join([title, source, category, heading, attachment_name, chunk_text or ""])
    values: list[str] = [
        title,
        source,
        category,
        heading,
        chunk_kind,
        attachment_name,
    ]
    for field in (
        "topics_json",
        "keywords_json",
        "applicable_colleges_json",
        "applicable_grades_json",
        "student_types_json",
    ):
        values.extend(str(item) for item in _json_load_list(row[field]))
    values.extend(_ai_metadata_terms(_json_load_dict(_row_get(row, "ai_metadata_json"))))
    for topic, aliases in SYNONYMS.items():
        if topic in text or any(alias in text for alias in aliases):
            values.append(topic)
            values.extend(aliases)
    for term in DOMAIN_TERMS:
        if term in text:
            values.append(term)
    if row["publish_date"]:
        values.append(str(row["publish_date"])[:4])
    values.extend(_important_title_terms(title))
    values.extend(_important_title_terms(heading))
    if chunk_kind == "attachment_text":
        values.extend(["附件正文", "附件内容", "表格", "名单", "下载"])
    elif chunk_kind == "attachment_list":
        values.extend(["附件列表", "下载", "材料"])
    elif chunk_kind == "title":
        values.extend(["标题", "原文", "通知"])
    return _dedupe_terms(values, limit=MAX_CHUNK_TAGS)


def _build_chunk_embedding_text(row: sqlite3.Row, chunk: dict[str, object], chunk_text: str, tags: list[str] | None = None) -> str:
    tags = tags or _build_chunk_tags(row, chunk, chunk_text)
    ai_metadata = _json_load_dict(_row_get(row, "ai_metadata_json"))
    parts = [
        f"标题：{row['title']}",
        f"来源：{row['source']}",
        f"栏目：{row['category'] or ''}",
        f"发布时间：{row['publish_date'] or ''}",
        f"小标题：{chunk.get('heading') or ''}",
        f"块类型：{chunk.get('chunk_kind') or 'body'}",
        f"附件：{chunk.get('attachment_name') or ''}",
        f"主题：{' '.join(str(item) for item in _json_load_list(row['topics_json']))}",
        f"关键词：{' '.join(str(item) for item in _json_load_list(row['keywords_json']))}",
        f"适用学院：{' '.join(str(item) for item in _json_load_list(row['applicable_colleges_json']))}",
        f"适用年级：{' '.join(str(item) for item in _json_load_list(row['applicable_grades_json']))}",
        f"适用身份：{' '.join(str(item) for item in _json_load_list(row['student_types_json']))}",
        f"标签：{' '.join(tags)}",
        f"AI元数据：{_ai_metadata_search_text(ai_metadata)}",
        f"正文：{chunk_text}",
    ]
    return "\n".join(part for part in parts if part.strip())[:MAX_EMBEDDING_TEXT_CHARS]


def _ai_metadata_terms(metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "topics",
        "keywords",
        "business_actions",
        "audience",
        "answerable_questions",
        "official_terms",
        "colleges",
        "grades",
        "student_types",
    ):
        raw = metadata.get(key)
        if isinstance(raw, list):
            values.extend(str(item) for item in raw if item)
        elif isinstance(raw, str):
            values.append(raw)
    for item in metadata.get("attachment_summaries") or []:
        if isinstance(item, dict):
            values.extend(str(item.get(key) or "") for key in ("name", "summary", "purpose"))
    return _dedupe_terms(values, limit=80)


def _ai_metadata_search_text(metadata: dict[str, Any]) -> str:
    if not metadata:
        return ""
    return " ".join(_ai_metadata_terms(metadata))[:1800]


def _important_title_terms(text: str) -> list[str]:
    terms: list[str] = []
    text = text or ""
    terms.extend(re.findall(r"20\d{2}(?:-\d{4})?学年|20\d{2}级|20\d{2}届|20\d{2}", text))
    terms.extend(re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", text))
    for run in re.findall(r"[\u4e00-\u9fff]{2,12}", text):
        if len(run) <= 6:
            terms.append(run)
    return terms


def split_document_chunks(title: str, body: str, attachments: list[dict[str, Any]] | None = None) -> list[dict[str, object]]:
    parts: list[dict[str, object]] = []
    if title.strip():
        parts.append({"text": title.strip(), "heading": "标题", "page": None, "chunk_kind": "title"})
    body = (body or "").strip()
    if any(item.get("text") for item in attachments or []):
        body = re.split(r"\n\n(?:附件正文摘录：|附件《)", body, maxsplit=1)[0].strip()
    if body:
        paragraphs = [item.strip() for item in body.splitlines() if item.strip()]
        current = ""
        heading = "正文"
        for paragraph in paragraphs:
            if len(paragraph) <= 40 and re.search(r"(流程|材料|时间|对象|条件|附件|通知|安排|说明)$", paragraph):
                heading = paragraph
            if current and len(current) + len(paragraph) > CHUNK_TARGET_CHARS:
                parts.append({"text": current.strip(), "heading": heading, "page": None, "chunk_kind": "body"})
                current = current[-CHUNK_OVERLAP_CHARS:] if len(current) > CHUNK_OVERLAP_CHARS else current
            current = f"{current}\n{paragraph}".strip() if current else paragraph
        if current:
            parts.append({"text": current.strip(), "heading": heading, "page": None, "chunk_kind": "body"})
    attachment_names = "、".join(item.get("name", "") for item in attachments or [] if item.get("name"))
    if attachment_names:
        parts.append({"text": f"附件：{attachment_names}", "heading": "附件", "page": None, "chunk_kind": "attachment_list"})
    for attachment in attachments or []:
        for part in attachment_text_parts(attachment):
            parts.append({**part, "chunk_kind": "attachment_text"})
    return [
        {**part, "text": str(part["text"])[:1800]}
        for part in parts
        if len(str(part.get("text") or "").strip()) >= 2
    ]


class DocumentStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    category TEXT,
                    publish_date TEXT,
                    body TEXT NOT NULL DEFAULT '',
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    applicable_colleges_json TEXT NOT NULL DEFAULT '[]',
                    applicable_grades_json TEXT NOT NULL DEFAULT '[]',
                    student_types_json TEXT NOT NULL DEFAULT '[]',
                    topics_json TEXT NOT NULL DEFAULT '[]',
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    ai_metadata_json TEXT NOT NULL DEFAULT '{}',
                    deadline TEXT,
                    content_hash TEXT,
                    crawled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    title,
                    body,
                    source,
                    category,
                    attachments,
                    topics,
                    content='documents',
                    content_rowid='id',
                    tokenize='unicode61'
                );

                CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                    INSERT INTO documents_fts(
                        rowid, title, body, source, category, attachments, topics
                    )
                    VALUES (
                        new.id,
                        new.title,
                        new.body,
                        new.source,
                        COALESCE(new.category, ''),
                        COALESCE(new.attachments_json, ''),
                        COALESCE(new.topics_json, '')
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                    INSERT INTO documents_fts(
                        documents_fts, rowid, title, body, source, category, attachments, topics
                    )
                    VALUES (
                        'delete',
                        old.id,
                        old.title,
                        old.body,
                        old.source,
                        COALESCE(old.category, ''),
                        COALESCE(old.attachments_json, ''),
                        COALESCE(old.topics_json, '')
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                    INSERT INTO documents_fts(
                        documents_fts, rowid, title, body, source, category, attachments, topics
                    )
                    VALUES (
                        'delete',
                        old.id,
                        old.title,
                        old.body,
                        old.source,
                        COALESCE(old.category, ''),
                        COALESCE(old.attachments_json, ''),
                        COALESCE(old.topics_json, '')
                    );
                    INSERT INTO documents_fts(
                        rowid, title, body, source, category, attachments, topics
                    )
                    VALUES (
                        new.id,
                        new.title,
                        new.body,
                        new.source,
                        COALESCE(new.category, ''),
                        COALESCE(new.attachments_json, ''),
                        COALESCE(new.topics_json, '')
                    );
                END;

                CREATE TABLE IF NOT EXISTS document_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    chunk_text TEXT NOT NULL,
                    search_text TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    publish_date TEXT,
                    heading TEXT,
                    page INTEGER,
                    attachment_name TEXT,
                    chunk_kind TEXT NOT NULL DEFAULT 'body',
                    token_count INTEGER NOT NULL DEFAULT 0,
                    topics_json TEXT NOT NULL DEFAULT '[]',
                    topics TEXT NOT NULL DEFAULT '',
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    embedding_json TEXT,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
                    UNIQUE(document_id, chunk_index)
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
                    title,
                    search_text,
                    chunk_text,
                    source,
                    topics,
                    content='document_chunks',
                    content_rowid='id',
                    tokenize='unicode61'
                );

                CREATE TRIGGER IF NOT EXISTS document_chunks_ai AFTER INSERT ON document_chunks BEGIN
                    INSERT INTO document_chunks_fts(rowid, title, search_text, chunk_text, source, topics)
                    VALUES (new.id, new.title, new.search_text, new.chunk_text, new.source, COALESCE(new.topics_json, ''));
                END;

                CREATE TRIGGER IF NOT EXISTS document_chunks_ad AFTER DELETE ON document_chunks BEGIN
                    INSERT INTO document_chunks_fts(document_chunks_fts, rowid, title, search_text, chunk_text, source, topics)
                    VALUES ('delete', old.id, old.title, old.search_text, old.chunk_text, old.source, COALESCE(old.topics_json, ''));
                END;

                CREATE TRIGGER IF NOT EXISTS document_chunks_au AFTER UPDATE ON document_chunks BEGIN
                    INSERT INTO document_chunks_fts(document_chunks_fts, rowid, title, search_text, chunk_text, source, topics)
                    VALUES ('delete', old.id, old.title, old.search_text, old.chunk_text, old.source, COALESCE(old.topics_json, ''));
                    INSERT INTO document_chunks_fts(rowid, title, search_text, chunk_text, source, topics)
                    VALUES (new.id, new.title, new.search_text, new.chunk_text, new.source, COALESCE(new.topics_json, ''));
                END;

                CREATE TABLE IF NOT EXISTS source_profiles (
                    source TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL DEFAULT 'other',
                    authority_weight REAL NOT NULL DEFAULT 1.0,
                    is_official INTEGER NOT NULL DEFAULT 1,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS crawl_tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    upserted INTEGER,
                    total_documents INTEGER,
                    error TEXT,
                    traceback TEXT
                );

                CREATE TABLE IF NOT EXISTS collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS collection_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_id INTEGER NOT NULL,
                    source_name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    seed_urls_json TEXT NOT NULL DEFAULT '[]',
                    include_path_prefixes_json TEXT NOT NULL DEFAULT '[]',
                    exclude_path_prefixes_json TEXT NOT NULL DEFAULT '[]',
                    max_depth INTEGER,
                    max_pages INTEGER,
                    days_back INTEGER,
                    is_enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(collection_id) REFERENCES collections(id) ON DELETE CASCADE
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_collection_sources_unique
                ON collection_sources(collection_id, source_name, base_url);

                CREATE TABLE IF NOT EXISTS collection_documents (
                    collection_id INTEGER NOT NULL,
                    document_id INTEGER NOT NULL,
                    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (collection_id, document_id),
                    FOREIGN KEY(collection_id) REFERENCES collections(id) ON DELETE CASCADE,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_collection_documents_document
                ON collection_documents(document_id);
                """
            )
            self._ensure_document_columns(conn)
            self._ensure_chunk_columns(conn)
            self._ensure_chunk_fts_schema(conn)
            self._ensure_crawl_task_columns(conn)
            self._ensure_source_profiles(conn)
            self._ensure_collection_schema(conn)
            self._ensure_chunks(conn)

    @staticmethod
    def _ensure_document_columns(conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
        if "keywords_json" not in columns:
            conn.execute("ALTER TABLE documents ADD COLUMN keywords_json TEXT NOT NULL DEFAULT '[]'")
        if "ai_metadata_json" not in columns:
            conn.execute("ALTER TABLE documents ADD COLUMN ai_metadata_json TEXT NOT NULL DEFAULT '{}'")

    @staticmethod
    def _ensure_chunk_columns(conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(document_chunks)").fetchall()}
        additions = {
            "search_text": "ALTER TABLE document_chunks ADD COLUMN search_text TEXT NOT NULL DEFAULT ''",
            "heading": "ALTER TABLE document_chunks ADD COLUMN heading TEXT",
            "page": "ALTER TABLE document_chunks ADD COLUMN page INTEGER",
            "attachment_name": "ALTER TABLE document_chunks ADD COLUMN attachment_name TEXT",
            "chunk_kind": "ALTER TABLE document_chunks ADD COLUMN chunk_kind TEXT NOT NULL DEFAULT 'body'",
            "token_count": "ALTER TABLE document_chunks ADD COLUMN token_count INTEGER NOT NULL DEFAULT 0",
            "topics": "ALTER TABLE document_chunks ADD COLUMN topics TEXT NOT NULL DEFAULT ''",
            "embedding_json": "ALTER TABLE document_chunks ADD COLUMN embedding_json TEXT",
            "tags_json": "ALTER TABLE document_chunks ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'",
        }
        for column, sql in additions.items():
            if column not in columns:
                conn.execute(sql)

    @staticmethod
    def _ensure_chunk_fts_schema(conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(document_chunks_fts)").fetchall()
        columns = [row["name"] for row in rows]
        trigger_rows = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'trigger'
              AND name IN ('document_chunks_ai', 'document_chunks_ad', 'document_chunks_au')
            """
        ).fetchall()
        triggers_have_search_text = len(trigger_rows) == 3 and all(
            "search_text" in (row["sql"] or "") for row in trigger_rows
        )
        if columns and "search_text" in columns and triggers_have_search_text:
            return
        DocumentStore._drop_chunk_fts_objects(conn)
        DocumentStore._create_chunk_fts_objects(conn)

    @staticmethod
    def _drop_chunk_fts_objects(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS document_chunks_ai;
            DROP TRIGGER IF EXISTS document_chunks_ad;
            DROP TRIGGER IF EXISTS document_chunks_au;
            DROP TABLE IF EXISTS document_chunks_fts;
            """
        )

    @staticmethod
    def _create_chunk_fts_objects(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE VIRTUAL TABLE document_chunks_fts USING fts5(
                title,
                search_text,
                chunk_text,
                source,
                topics,
                content='document_chunks',
                content_rowid='id',
                tokenize='unicode61'
            );

            CREATE TRIGGER document_chunks_ai AFTER INSERT ON document_chunks BEGIN
                INSERT INTO document_chunks_fts(rowid, title, search_text, chunk_text, source, topics)
                VALUES (new.id, new.title, new.search_text, new.chunk_text, new.source, COALESCE(new.topics_json, ''));
            END;

            CREATE TRIGGER document_chunks_ad AFTER DELETE ON document_chunks BEGIN
                INSERT INTO document_chunks_fts(document_chunks_fts, rowid, title, search_text, chunk_text, source, topics)
                VALUES ('delete', old.id, old.title, old.search_text, old.chunk_text, old.source, COALESCE(old.topics_json, ''));
            END;

            CREATE TRIGGER document_chunks_au AFTER UPDATE ON document_chunks BEGIN
                INSERT INTO document_chunks_fts(document_chunks_fts, rowid, title, search_text, chunk_text, source, topics)
                VALUES ('delete', old.id, old.title, old.search_text, old.chunk_text, old.source, COALESCE(old.topics_json, ''));
                INSERT INTO document_chunks_fts(rowid, title, search_text, chunk_text, source, topics)
                VALUES (new.id, new.title, new.search_text, new.chunk_text, new.source, COALESCE(new.topics_json, ''));
            END;
            """
        )

    @staticmethod
    def _ensure_crawl_task_columns(conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(crawl_tasks)").fetchall()}
        if "collection_id" not in columns:
            conn.execute("ALTER TABLE crawl_tasks ADD COLUMN collection_id INTEGER")

    def _ensure_collection_schema(self, conn: sqlite3.Connection) -> None:
        collection_row = conn.execute(
            "SELECT id FROM collections WHERE slug = ?",
            (DEFAULT_COLLECTION_SLUG,),
        ).fetchone()
        if collection_row is None:
            conn.execute(
                """
                INSERT INTO collections (name, slug, description, is_enabled, updated_at)
                VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
                """,
                (
                    DEFAULT_COLLECTION_NAME,
                    DEFAULT_COLLECTION_SLUG,
                    "Default dataset for the built-in search index.",
                ),
            )
            collection_row = conn.execute(
                "SELECT id FROM collections WHERE slug = ?",
                (DEFAULT_COLLECTION_SLUG,),
            ).fetchone()

        default_collection_id = int(collection_row["id"])
        source_count = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM collection_sources WHERE collection_id = ?",
                (default_collection_id,),
            ).fetchone()["n"]
        )
        if source_count == 0:
            from .crawler.seu_sites import configured_seed_sites

            for site in configured_seed_sites():
                conn.execute(
                    """
                    INSERT INTO collection_sources (
                        collection_id, source_name, base_url, seed_urls_json,
                        include_path_prefixes_json, exclude_path_prefixes_json,
                        max_depth, max_pages, days_back, is_enabled, updated_at
                    )
                    VALUES (?, ?, ?, ?, '[]', '[]', ?, ?, ?, 1, CURRENT_TIMESTAMP)
                    """,
                    (
                        default_collection_id,
                        site["source"],
                        site["base"],
                        _json_dump(site["seeds"]),
                        site.get("max_depth"),
                        site.get("max_pages"),
                        site.get("days_back"),
                    ),
                )

        membership_count = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM collection_documents WHERE collection_id = ?",
                (default_collection_id,),
            ).fetchone()["n"]
        )
        if membership_count == 0:
            conn.execute(
                """
                INSERT OR IGNORE INTO collection_documents (collection_id, document_id)
                SELECT ?, id
                FROM documents
                """,
                (default_collection_id,),
            )

    @staticmethod
    def _ensure_source_profiles(conn: sqlite3.Connection) -> None:
        defaults = [
            ("教务处", "academic_affairs", 3.0, 1, "本科教务事项优先来源"),
            ("研究生院", "graduate_school", 2.8, 1, "研究生培养与学位事项优先来源"),
            ("学校官网", "university", 1.6, 1, "全校公开通知与新闻来源"),
        ]
        for source, source_type, weight, official, notes in defaults:
            conn.execute(
                """
                INSERT INTO source_profiles (source, source_type, authority_weight, is_official, notes)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source) DO NOTHING
                """,
                (source, source_type, weight, official, notes),
            )
        sources = conn.execute("SELECT DISTINCT source FROM documents").fetchall()
        for row in sources:
            source = row["source"]
            source_type = "college" if "学院" in source else "other"
            weight = 2.2 if source_type == "college" else 1.0
            conn.execute(
                """
                INSERT INTO source_profiles (source, source_type, authority_weight, is_official, notes)
                VALUES (?, ?, ?, 1, NULL)
                ON CONFLICT(source) DO NOTHING
                """,
                (source, source_type, weight),
            )

    def _ensure_chunks(self, conn: sqlite3.Connection) -> None:
        chunk_count = int(conn.execute("SELECT COUNT(*) AS n FROM document_chunks").fetchone()["n"])
        zero_tokens = int(
            conn.execute("SELECT COUNT(*) AS n FROM document_chunks WHERE token_count = 0").fetchone()["n"]
        )
        empty_search_text = int(
            conn.execute("SELECT COUNT(*) AS n FROM document_chunks WHERE search_text = ''").fetchone()["n"]
        )
        empty_topics = int(
            conn.execute("SELECT COUNT(*) AS n FROM document_chunks WHERE topics = '' AND topics_json != '[]'").fetchone()["n"]
        )
        document_count = int(conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"])
        docs_with_title_chunks = int(
            conn.execute(
                "SELECT COUNT(DISTINCT document_id) AS n FROM document_chunks WHERE chunk_kind = 'title'"
            ).fetchone()["n"]
        )
        if (
            chunk_count > 0
            and zero_tokens == 0
            and empty_search_text == 0
            and empty_topics == 0
            and docs_with_title_chunks >= document_count
        ):
            return
        self.rebuild_all_chunks(conn)

    def rebuild_all_chunks(self, conn: sqlite3.Connection | None = None) -> int:
        if conn is not None:
            docs = conn.execute("SELECT * FROM documents").fetchall()
            self._drop_chunk_fts_objects(conn)
            conn.execute("DELETE FROM document_chunks")
            self._create_chunk_fts_objects(conn)
            for doc in docs:
                self._insert_chunks(conn, doc["id"], doc)
            return len(docs)
        with self.connect() as owned_conn:
            return self.rebuild_all_chunks(owned_conn)

    def upsert_documents(self, docs: Iterable[SourceDocument]) -> int:
        count = 0
        with self.connect() as conn:
            for doc in docs:
                doc.body = clean_body_text(doc.body, title=doc.title)
                doc = enrich_document(doc)
                body = doc.body
                content_hash = make_content_hash(doc.title, body, doc.url, doc.attachments)
                conn.execute(
                    """
                    INSERT INTO documents (
                        title, url, source, category, publish_date, body,
                        attachments_json, applicable_colleges_json,
                        applicable_grades_json, student_types_json, topics_json,
                        keywords_json, deadline, content_hash, crawled_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(url) DO UPDATE SET
                        title = excluded.title,
                        source = excluded.source,
                        category = excluded.category,
                        publish_date = excluded.publish_date,
                        body = excluded.body,
                        attachments_json = excluded.attachments_json,
                        applicable_colleges_json = excluded.applicable_colleges_json,
                        applicable_grades_json = excluded.applicable_grades_json,
                        student_types_json = excluded.student_types_json,
                        topics_json = excluded.topics_json,
                        keywords_json = excluded.keywords_json,
                        ai_metadata_json = CASE
                            WHEN documents.content_hash = excluded.content_hash THEN documents.ai_metadata_json
                            ELSE '{}'
                        END,
                        deadline = excluded.deadline,
                        content_hash = excluded.content_hash,
                        crawled_at = CURRENT_TIMESTAMP
                    """,
                    (
                        doc.title,
                        doc.url,
                        doc.source,
                        doc.category,
                        _date_to_str(doc.publish_date),
                        body,
                        _json_dump(doc.attachments),
                        _json_dump(doc.applicable_colleges),
                        _json_dump(doc.applicable_grades),
                        _json_dump(doc.student_types),
                        _json_dump(doc.topics),
                        _json_dump(doc.keywords),
                        _date_to_str(doc.deadline),
                        content_hash,
                    ),
                )
                row = conn.execute("SELECT * FROM documents WHERE url = ?", (doc.url,)).fetchone()
                if row:
                    self._replace_chunks(conn, row["id"], row)
                count += 1
        return count

    def _replace_chunks(self, conn: sqlite3.Connection, document_id: int, row: sqlite3.Row) -> None:
        conn.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
        self._insert_chunks(conn, document_id, row)

    def _insert_chunks(self, conn: sqlite3.Connection, document_id: int, row: sqlite3.Row) -> None:
        attachments = _json_load_list(row["attachments_json"])
        chunks = split_document_chunks(row["title"], row["body"], attachments)
        for index, chunk in enumerate(chunks):
            chunk_text = str(chunk.get("text") or "")
            chunk_tags = _build_chunk_tags(row, chunk, chunk_text)
            conn.execute(
                """
                INSERT INTO document_chunks (
                    document_id, chunk_index, title, chunk_text, search_text, source,
                    publish_date, heading, page, attachment_name, chunk_kind,
                    token_count, topics_json, topics, keywords_json,
                    tags_json, embedding_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    index,
                    row["title"],
                    chunk_text,
                    _build_chunk_search_text(row, chunk, chunk_text),
                    row["source"],
                    row["publish_date"],
                    chunk.get("heading"),
                    chunk.get("page"),
                    chunk.get("attachment_name"),
                    chunk.get("chunk_kind") or "body",
                    _estimate_token_count(chunk_text),
                    row["topics_json"],
                    " ".join(str(item) for item in _json_load_list(row["topics_json"])),
                    row["keywords_json"],
                    _json_dump(chunk_tags),
                    embedding_to_json(embed_text(_build_chunk_embedding_text(row, chunk, chunk_text, chunk_tags))),
                ),
            )

    def list_collections(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = """
            SELECT c.*,
                   COUNT(DISTINCT cs.id) AS source_count,
                   COUNT(DISTINCT cd.document_id) AS document_count,
                   MAX(t.updated_at) AS last_crawled_at
            FROM collections c
            LEFT JOIN collection_sources cs
              ON cs.collection_id = c.id
             AND cs.is_enabled = 1
            LEFT JOIN collection_documents cd
              ON cd.collection_id = c.id
            LEFT JOIN crawl_tasks t
              ON t.collection_id = c.id
             AND t.status = 'completed'
            WHERE (? = 0 OR c.is_enabled = 1)
            GROUP BY c.id
            ORDER BY CASE WHEN c.slug = ? THEN 0 ELSE 1 END, c.updated_at DESC, c.id DESC
        """
        with self.connect() as conn:
            rows = conn.execute(sql, (1 if enabled_only else 0, DEFAULT_COLLECTION_SLUG)).fetchall()
        return [_collection_row_to_dict(row) for row in rows]

    def get_collection(self, collection_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT c.*,
                       COUNT(DISTINCT cs.id) AS source_count,
                       COUNT(DISTINCT cd.document_id) AS document_count,
                       MAX(t.updated_at) AS last_crawled_at
                FROM collections c
                LEFT JOIN collection_sources cs
                  ON cs.collection_id = c.id
                 AND cs.is_enabled = 1
                LEFT JOIN collection_documents cd
                  ON cd.collection_id = c.id
                LEFT JOIN crawl_tasks t
                  ON t.collection_id = c.id
                 AND t.status = 'completed'
                WHERE c.id = ?
                GROUP BY c.id
                """,
                (collection_id,),
            ).fetchone()
        return _collection_row_to_dict(row) if row else None

    def get_default_collection(self, enabled_only: bool = False) -> dict[str, Any] | None:
        with self.connect() as conn:
            query = """
                SELECT c.*,
                       COUNT(DISTINCT cs.id) AS source_count,
                       COUNT(DISTINCT cd.document_id) AS document_count,
                       MAX(t.updated_at) AS last_crawled_at
                FROM collections c
                LEFT JOIN collection_sources cs
                  ON cs.collection_id = c.id
                 AND cs.is_enabled = 1
                LEFT JOIN collection_documents cd
                  ON cd.collection_id = c.id
                LEFT JOIN crawl_tasks t
                  ON t.collection_id = c.id
                 AND t.status = 'completed'
                WHERE c.slug = ?
            """
            params: list[Any] = [DEFAULT_COLLECTION_SLUG]
            if enabled_only:
                query += " AND c.is_enabled = 1"
            query += " GROUP BY c.id LIMIT 1"
            row = conn.execute(query, tuple(params)).fetchone()
        return _collection_row_to_dict(row) if row else None

    def create_collection(
        self,
        name: str,
        description: str = "",
        *,
        slug: str | None = None,
        is_enabled: bool = True,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Collection name is required.")
        with self.connect() as conn:
            slug_value = self._next_collection_slug(conn, slug or name)
            cursor = conn.execute(
                """
                INSERT INTO collections (name, slug, description, is_enabled, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (name, slug_value, description.strip(), 1 if is_enabled else 0),
            )
            collection_id = int(cursor.lastrowid)
        collection = self.get_collection(collection_id)
        if collection is None:
            raise ValueError("Failed to create collection.")
        return collection

    def update_collection(
        self,
        collection_id: int,
        *,
        name: str,
        description: str = "",
        is_enabled: bool = True,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            existing = conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
            if existing is None:
                return None
            conn.execute(
                """
                UPDATE collections
                SET name = ?, description = ?, is_enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (name.strip(), description.strip(), 1 if is_enabled else 0, collection_id),
            )
        return self.get_collection(collection_id)

    def delete_collection(self, collection_id: int) -> bool:
        with self.connect() as conn:
            existing = conn.execute("SELECT slug FROM collections WHERE id = ?", (collection_id,)).fetchone()
            if existing is None:
                return False
            if existing["slug"] == DEFAULT_COLLECTION_SLUG:
                raise ValueError("The default collection cannot be deleted.")
            cursor = conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        return bool(cursor.rowcount)

    def list_collection_sources(self, collection_id: int, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = """
            SELECT *
            FROM collection_sources
            WHERE collection_id = ?
              AND (? = 0 OR is_enabled = 1)
            ORDER BY is_enabled DESC, updated_at DESC, id DESC
        """
        with self.connect() as conn:
            rows = conn.execute(sql, (collection_id, 1 if enabled_only else 0)).fetchall()
        return [_collection_source_row_to_dict(row) for row in rows]

    def create_collection_source(
        self,
        collection_id: int,
        *,
        source_name: str,
        base_url: str,
        seed_urls: list[str],
        include_path_prefixes: list[str] | None = None,
        exclude_path_prefixes: list[str] | None = None,
        max_depth: int | None = None,
        max_pages: int | None = None,
        days_back: int | None = None,
        is_enabled: bool = True,
    ) -> dict[str, Any]:
        payload = _normalized_collection_source_payload(
            collection_id=collection_id,
            source_name=source_name,
            base_url=base_url,
            seed_urls=seed_urls,
            include_path_prefixes=include_path_prefixes,
            exclude_path_prefixes=exclude_path_prefixes,
            max_depth=max_depth,
            max_pages=max_pages,
            days_back=days_back,
            is_enabled=is_enabled,
        )
        with self.connect() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO collection_sources (
                        collection_id, source_name, base_url, seed_urls_json,
                        include_path_prefixes_json, exclude_path_prefixes_json,
                        max_depth, max_pages, days_back, is_enabled, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        payload["collection_id"],
                        payload["source_name"],
                        payload["base_url"],
                        _json_dump(payload["seed_urls"]),
                        _json_dump(payload["include_path_prefixes"]),
                        _json_dump(payload["exclude_path_prefixes"]),
                        payload["max_depth"],
                        payload["max_pages"],
                        payload["days_back"],
                        1 if payload["is_enabled"] else 0,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("A source with the same name and base URL already exists in this collection.") from exc
            source_id = int(cursor.lastrowid)
            row = conn.execute("SELECT * FROM collection_sources WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise ValueError("Failed to create collection source.")
        return _collection_source_row_to_dict(row)

    def update_collection_source(
        self,
        source_id: int,
        *,
        source_name: str,
        base_url: str,
        seed_urls: list[str],
        include_path_prefixes: list[str] | None = None,
        exclude_path_prefixes: list[str] | None = None,
        max_depth: int | None = None,
        max_pages: int | None = None,
        days_back: int | None = None,
        is_enabled: bool = True,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            existing = conn.execute("SELECT * FROM collection_sources WHERE id = ?", (source_id,)).fetchone()
            if existing is None:
                return None
            payload = _normalized_collection_source_payload(
                collection_id=int(existing["collection_id"]),
                source_name=source_name,
                base_url=base_url,
                seed_urls=seed_urls,
                include_path_prefixes=include_path_prefixes,
                exclude_path_prefixes=exclude_path_prefixes,
                max_depth=max_depth,
                max_pages=max_pages,
                days_back=days_back,
                is_enabled=is_enabled,
            )
            try:
                conn.execute(
                    """
                    UPDATE collection_sources
                    SET source_name = ?,
                        base_url = ?,
                        seed_urls_json = ?,
                        include_path_prefixes_json = ?,
                        exclude_path_prefixes_json = ?,
                        max_depth = ?,
                        max_pages = ?,
                        days_back = ?,
                        is_enabled = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        payload["source_name"],
                        payload["base_url"],
                        _json_dump(payload["seed_urls"]),
                        _json_dump(payload["include_path_prefixes"]),
                        _json_dump(payload["exclude_path_prefixes"]),
                        payload["max_depth"],
                        payload["max_pages"],
                        payload["days_back"],
                        1 if payload["is_enabled"] else 0,
                        source_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("A source with the same name and base URL already exists in this collection.") from exc
            row = conn.execute("SELECT * FROM collection_sources WHERE id = ?", (source_id,)).fetchone()
        return _collection_source_row_to_dict(row) if row else None

    def delete_collection_source(self, source_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM collection_sources WHERE id = ?", (source_id,))
        return bool(cursor.rowcount)

    def get_collection_crawl_sites(self, collection_id: int) -> list[dict[str, Any]]:
        sources = self.list_collection_sources(collection_id, enabled_only=True)
        return [
            {
                "source": source["source_name"],
                "base": source["base_url"],
                "seeds": source["seed_urls"] or [source["base_url"]],
                "include_path_prefixes": source["include_path_prefixes"],
                "exclude_path_prefixes": source["exclude_path_prefixes"],
                "max_depth": source["max_depth"],
                "max_pages": source["max_pages"],
                "days_back": source["days_back"],
            }
            for source in sources
        ]

    def replace_collection_documents(self, collection_id: int, urls: Iterable[str]) -> int:
        clean_urls = [url.strip() for url in urls if isinstance(url, str) and url.strip()]
        with self.connect() as conn:
            document_ids: list[int] = []
            if clean_urls:
                for index in range(0, len(clean_urls), 200):
                    chunk = clean_urls[index : index + 200]
                    placeholders = ",".join("?" for _ in chunk)
                    rows = conn.execute(
                        f"SELECT id FROM documents WHERE url IN ({placeholders})",
                        chunk,
                    ).fetchall()
                    document_ids.extend(int(row["id"]) for row in rows)
            conn.execute("DELETE FROM collection_documents WHERE collection_id = ?", (collection_id,))
            if document_ids:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO collection_documents (collection_id, document_id)
                    VALUES (?, ?)
                    """,
                    [(collection_id, document_id) for document_id in document_ids],
                )
        return len(document_ids)

    def prune_orphan_documents(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM documents
                WHERE id NOT IN (
                    SELECT DISTINCT document_id
                    FROM collection_documents
                )
                """
            )
        return cursor.rowcount if cursor.rowcount is not None else 0

    def count_documents(self, collection_id: int | None = None) -> int:
        with self.connect() as conn:
            if collection_id is None:
                row = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM collection_documents
                    WHERE collection_id = ?
                    """,
                    (collection_id,),
                ).fetchone()
            return int(row["n"])

    def get_index_stats(self, collection_id: int | None = None) -> dict[str, int]:
        with self.connect() as conn:
            if collection_id is None:
                document_count = int(conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"])
                chunk_count = int(conn.execute("SELECT COUNT(*) AS n FROM document_chunks").fetchone()["n"])
                attachment_text_chunks = int(
                    conn.execute(
                        "SELECT COUNT(*) AS n FROM document_chunks WHERE chunk_kind = 'attachment_text'"
                    ).fetchone()["n"]
                )
                rows = conn.execute(
                    """
                    SELECT attachments_json
                    FROM documents
                    WHERE attachments_json IS NOT NULL
                      AND attachments_json != '[]'
                    """
                ).fetchall()
            else:
                document_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS n
                        FROM collection_documents
                        WHERE collection_id = ?
                        """,
                        (collection_id,),
                    ).fetchone()["n"]
                )
                chunk_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS n
                        FROM document_chunks c
                        JOIN collection_documents cd
                          ON cd.document_id = c.document_id
                        WHERE cd.collection_id = ?
                        """,
                        (collection_id,),
                    ).fetchone()["n"]
                )
                attachment_text_chunks = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS n
                        FROM document_chunks c
                        JOIN collection_documents cd
                          ON cd.document_id = c.document_id
                        WHERE cd.collection_id = ?
                          AND c.chunk_kind = 'attachment_text'
                        """,
                        (collection_id,),
                    ).fetchone()["n"]
                )
                rows = conn.execute(
                    """
                    SELECT d.attachments_json
                    FROM documents d
                    JOIN collection_documents cd
                      ON cd.document_id = d.id
                    WHERE cd.collection_id = ?
                      AND d.attachments_json IS NOT NULL
                      AND d.attachments_json != '[]'
                    """,
                    (collection_id,),
                ).fetchall()
        attachments = [item for row in rows for item in _json_load_list(row["attachments_json"])]
        return {
            "documents": document_count,
            "chunks": chunk_count,
            "docs_with_attachments": len(rows),
            "attachments": len(attachments),
            "attachments_with_text": sum(1 for item in attachments if item.get("text")),
            "attachments_with_pages": sum(1 for item in attachments if item.get("pages")),
            "attachments_with_sheets": sum(1 for item in attachments if item.get("sheets")),
            "attachment_text_chunks": attachment_text_chunks,
        }

    def get_source_profiles(self) -> dict[str, dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM source_profiles").fetchall()
        return {row["source"]: dict(row) for row in rows}

    def backfill_chunk_embeddings(self, limit: int | None = None, refresh: bool = False) -> int:
        sql = """
            SELECT d.*,
                   c.id AS chunk_id,
                   c.chunk_text AS chunk_text,
                   c.heading AS heading,
                   c.page AS page,
                   c.attachment_name AS attachment_name,
                   c.chunk_kind AS chunk_kind,
                   c.tags_json AS tags_json,
                   c.embedding_json AS embedding_json
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE 1 = 1
            ORDER BY c.id
        """
        params: tuple = ()
        if not refresh:
            sql = sql.replace("WHERE 1 = 1", "WHERE embedding_json IS NULL OR embedding_json = ''")
        if limit:
            sql += " LIMIT ?"
            params = (limit,)
        with self.connect() as conn:
            candidate_rows = conn.execute(sql, params).fetchall()
            rows = [
                row
                for row in candidate_rows
                if refresh or not embedding_json_matches_current(row["embedding_json"])
            ]
            batch_size = max(1, settings.embedding_batch_size)
            for index in range(0, len(rows), batch_size):
                batch = rows[index : index + batch_size]
                texts = [
                    _build_chunk_embedding_text(row, _chunk_from_row(row), row["chunk_text"], _json_load_list(row["tags_json"]))
                    for row in batch
                ]
                vectors = embed_texts(texts)
                for row, vector in zip(batch, vectors):
                    conn.execute(
                        "UPDATE document_chunks SET embedding_json = ? WHERE id = ?",
                        (embedding_to_json(vector), row["chunk_id"]),
                    )
        return len(rows)

    def backfill_chunk_metadata(
        self,
        limit: int | None = None,
        refresh_embeddings: bool = False,
    ) -> int:
        sql = """
            SELECT d.*,
                   c.id AS chunk_id,
                   c.chunk_text AS chunk_text,
                   c.heading AS heading,
                   c.page AS page,
                   c.attachment_name AS attachment_name,
                   c.chunk_kind AS chunk_kind,
                   c.search_text AS search_text,
                   c.tags_json AS tags_json,
                   c.embedding_json AS embedding_json
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            ORDER BY c.id
        """
        params: tuple = ()
        if limit:
            sql += " LIMIT ?"
            params = (limit,)
        with self.connect() as conn:
            self._ensure_chunk_columns(conn)
            rows = conn.execute(sql, params).fetchall()
            updated = 0
            batch_size = max(1, settings.embedding_batch_size)
            embedding_batch: list[tuple[sqlite3.Row, str]] = []

            def flush_embeddings() -> None:
                nonlocal embedding_batch
                if not embedding_batch:
                    return
                vectors = embed_texts([text for _, text in embedding_batch])
                for row, vector in zip([item[0] for item in embedding_batch], vectors):
                    conn.execute(
                        "UPDATE document_chunks SET embedding_json = ? WHERE id = ?",
                        (embedding_to_json(vector), row["chunk_id"]),
                    )
                embedding_batch = []

            for row in rows:
                chunk = _chunk_from_row(row)
                chunk_text = row["chunk_text"] or ""
                tags = _build_chunk_tags(row, chunk, chunk_text)
                search_text = _build_chunk_search_text(row, chunk, chunk_text)
                should_refresh_embedding = refresh_embeddings or not embedding_json_matches_current(row["embedding_json"])
                conn.execute(
                    """
                    UPDATE document_chunks
                    SET search_text = ?,
                        tags_json = ?,
                        token_count = ?,
                        topics = ?
                    WHERE id = ?
                    """,
                    (
                        search_text,
                        _json_dump(tags),
                        _estimate_token_count(chunk_text),
                        " ".join(str(item) for item in _json_load_list(row["topics_json"])),
                        row["chunk_id"],
                    ),
                )
                if should_refresh_embedding:
                    embedding_batch.append((row, _build_chunk_embedding_text(row, chunk, chunk_text, tags)))
                    if len(embedding_batch) >= batch_size:
                        flush_embeddings()
                updated += 1
            flush_embeddings()
        return updated

    def upsert_crawl_task(self, task: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO crawl_tasks (
                    task_id, collection_id, status, created_at, updated_at, upserted,
                    total_documents, error, traceback
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    collection_id = excluded.collection_id,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    upserted = excluded.upserted,
                    total_documents = excluded.total_documents,
                    error = excluded.error,
                    traceback = excluded.traceback
                """,
                (
                    task["task_id"],
                    task.get("collection_id"),
                    task["status"],
                    task["created_at"],
                    task["updated_at"],
                    task.get("upserted"),
                    task.get("total_documents"),
                    task.get("error"),
                    task.get("traceback"),
                ),
            )

    def get_crawl_task(self, task_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM crawl_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def get_document(self, doc_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()

    def iter_documents_with_attachments(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = """
            SELECT *
            FROM documents
            WHERE attachments_json IS NOT NULL
              AND attachments_json != '[]'
            ORDER BY publish_date DESC, id DESC
        """
        params: tuple = ()
        if limit:
            sql += " LIMIT ?"
            params = (limit,)
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def update_document_attachments(self, doc_id: int, attachments: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if not row:
                return
            base_body = row["body"] or ""
            marker = "\n\n附件正文摘录：\n"
            base_body = base_body.split(marker, 1)[0].strip()
            conn.execute(
                """
                UPDATE documents
                SET body = ?,
                    attachments_json = ?,
                    keywords_json = ?,
                    crawled_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    base_body,
                    _json_dump(attachments),
                    row["keywords_json"],
                    doc_id,
                ),
            )
            updated = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if updated:
                self._replace_chunks(conn, doc_id, updated)

    def update_document_clean_body(
        self,
        doc_id: int,
        body: str,
        *,
        keywords: list[str] | None = None,
        topics: list[str] | None = None,
        applicable_colleges: list[str] | None = None,
        applicable_grades: list[str] | None = None,
        student_types: list[str] | None = None,
        deadline: date | None = None,
        replace_deadline: bool = False,
        clear_ai_metadata: bool = True,
    ) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if not row:
                return
            clean_body = clean_body_text(body, title=row["title"])
            ai_metadata_json = "{}" if clear_ai_metadata else row["ai_metadata_json"]
            deadline_value = _date_to_str(deadline) if replace_deadline else (_date_to_str(deadline) if deadline else row["deadline"])
            conn.execute(
                """
                UPDATE documents
                SET body = ?,
                    applicable_colleges_json = ?,
                    applicable_grades_json = ?,
                    student_types_json = ?,
                    topics_json = ?,
                    keywords_json = ?,
                    deadline = ?,
                    content_hash = ?,
                    ai_metadata_json = ?,
                    crawled_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    clean_body,
                    _json_dump(applicable_colleges if applicable_colleges is not None else _json_load_list(row["applicable_colleges_json"])),
                    _json_dump(applicable_grades if applicable_grades is not None else _json_load_list(row["applicable_grades_json"])),
                    _json_dump(student_types if student_types is not None else _json_load_list(row["student_types_json"])),
                    _json_dump(topics if topics is not None else _json_load_list(row["topics_json"])),
                    _json_dump(keywords if keywords is not None else _json_load_list(row["keywords_json"])),
                    deadline_value,
                    make_content_hash(row["title"], clean_body, row["url"], _json_load_list(row["attachments_json"])),
                    ai_metadata_json,
                    doc_id,
                ),
            )
            updated = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if updated:
                self._replace_chunks(conn, doc_id, updated)

    def update_document_ai_metadata(self, doc_id: int, metadata: dict[str, Any]) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if not row:
                return
            conn.execute(
                """
                UPDATE documents
                SET ai_metadata_json = ?,
                    crawled_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (_json_dump(metadata), doc_id),
            )
            updated = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if updated:
                self._replace_chunks(conn, doc_id, updated)

    def delete_documents_by_urls(self, urls: Iterable[str]) -> int:
        url_list = [url for url in urls if url]
        if not url_list:
            return 0
        deleted = 0
        with self.connect() as conn:
            for index in range(0, len(url_list), 200):
                chunk = url_list[index : index + 200]
                placeholders = ",".join("?" for _ in chunk)
                cursor = conn.execute(f"DELETE FROM documents WHERE url IN ({placeholders})", chunk)
                deleted += cursor.rowcount if cursor.rowcount is not None else 0
        return deleted

    def delete_duplicate_documents(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM documents
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM documents
                    GROUP BY title, body
                )
                """
            )
            return cursor.rowcount if cursor.rowcount is not None else 0

    @staticmethod
    def _next_collection_slug(conn: sqlite3.Connection, raw_value: str) -> str:
        base = _slugify(raw_value) or "collection"
        slug = base
        suffix = 2
        while conn.execute("SELECT 1 FROM collections WHERE slug = ?", (slug,)).fetchone():
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug


def _row_get(row: sqlite3.Row, key: str, default: object = None) -> object:
    return row[key] if key in row.keys() else default


def _row_bool(row: sqlite3.Row, key: str, default: bool = False) -> bool:
    value = _row_get(row, key, 1 if default else 0)
    return bool(int(value or 0))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug[:60]


def _normalized_list(values: Iterable[str] | None) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        item = re.sub(r"\s+", " ", str(value or "")).strip()
        if not item:
            continue
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        output.append(item)
    return output


def _normalized_collection_source_payload(
    *,
    collection_id: int,
    source_name: str,
    base_url: str,
    seed_urls: list[str],
    include_path_prefixes: list[str] | None,
    exclude_path_prefixes: list[str] | None,
    max_depth: int | None,
    max_pages: int | None,
    days_back: int | None,
    is_enabled: bool,
) -> dict[str, Any]:
    normalized_source_name = source_name.strip()
    normalized_base_url = base_url.strip()
    normalized_seeds = _normalized_list(seed_urls) or [normalized_base_url]
    if not normalized_source_name:
        raise ValueError("Source name is required.")
    if not normalized_base_url:
        raise ValueError("Base URL is required.")
    return {
        "collection_id": collection_id,
        "source_name": normalized_source_name,
        "base_url": normalized_base_url,
        "seed_urls": normalized_seeds,
        "include_path_prefixes": _normalized_list(include_path_prefixes),
        "exclude_path_prefixes": _normalized_list(exclude_path_prefixes),
        "max_depth": max_depth,
        "max_pages": max_pages,
        "days_back": days_back,
        "is_enabled": is_enabled,
    }


def _collection_source_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "collection_id": int(row["collection_id"]),
        "source_name": row["source_name"],
        "base_url": row["base_url"],
        "seed_urls": _json_load_list(row["seed_urls_json"]),
        "include_path_prefixes": _json_load_list(row["include_path_prefixes_json"]),
        "exclude_path_prefixes": _json_load_list(row["exclude_path_prefixes_json"]),
        "max_depth": row["max_depth"],
        "max_pages": row["max_pages"],
        "days_back": row["days_back"],
        "is_enabled": _row_bool(row, "is_enabled", True),
        "created_at": _row_get(row, "created_at"),
        "updated_at": _row_get(row, "updated_at"),
    }


def _collection_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "slug": row["slug"],
        "description": row["description"] or "",
        "is_enabled": _row_bool(row, "is_enabled", True),
        "source_count": int(_row_get(row, "source_count", 0) or 0),
        "document_count": int(_row_get(row, "document_count", 0) or 0),
        "last_crawled_at": _row_get(row, "last_crawled_at"),
        "updated_at": _row_get(row, "updated_at"),
    }


def _chunk_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "text": _row_get(row, "chunk_text", "") or "",
        "heading": _row_get(row, "heading"),
        "page": _row_get(row, "page"),
        "attachment_name": _row_get(row, "attachment_name"),
        "chunk_kind": _row_get(row, "chunk_kind") or "body",
    }


def row_to_hit(row: sqlite3.Row, score: float, snippet: str) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "url": row["url"],
        "source": row["source"],
        "category": row["category"],
        "publish_date": row["publish_date"],
        "snippet": snippet,
        "score": score,
        "relevance_note": None,
        "attachments": _json_load_list(row["attachments_json"]),
        "applicable_colleges": _json_load_list(row["applicable_colleges_json"]),
        "applicable_grades": _json_load_list(row["applicable_grades_json"]),
        "student_types": _json_load_list(row["student_types_json"]),
        "topics": _json_load_list(row["topics_json"]),
        "keywords": _json_load_list(row["keywords_json"]),
        "deadline": row["deadline"],
        "heading": _row_get(row, "heading"),
        "page": _row_get(row, "page"),
        "attachment_name": _row_get(row, "attachment_name"),
        "chunk_kind": _row_get(row, "chunk_kind"),
        "chunk_tags": _json_load_list(_row_get(row, "tags_json")),
        "matched_chunk_text": _row_get(row, "chunk_text"),
    }
