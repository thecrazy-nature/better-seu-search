from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


class Settings:
    def __init__(self) -> None:
        provider = os.getenv("AI_PROVIDER", "").strip().lower()
        if not provider:
            provider = "deepseek" if os.getenv("DEEPSEEK_API_KEY") else "openai"
        self.ai_provider = provider

        self.ai_api_key = (
            os.getenv("AI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ).strip()
        default_base_url = "https://api.deepseek.com" if provider == "deepseek" else ""
        self.ai_base_url = (os.getenv("AI_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or default_base_url).strip()
        default_model = "deepseek-v4-flash" if provider == "deepseek" else "gpt-4o-mini"
        self.ai_model = (
            os.getenv("AI_MODEL")
            or os.getenv("DEEPSEEK_MODEL")
            or os.getenv("OPENAI_MODEL")
            or default_model
        ).strip()
        self.ai_timeout_seconds = float(os.getenv("AI_TIMEOUT_SECONDS", "15"))
        self.ai_max_retries = int(os.getenv("AI_MAX_RETRIES", "0"))
        self.ai_planner_mode = os.getenv("AI_PLANNER_MODE", "always").strip().lower()
        self.ai_reranker_mode = os.getenv("AI_RERANKER_MODE", "off").strip().lower()
        self.ai_evidence_judge_mode = os.getenv("AI_EVIDENCE_JUDGE_MODE", "off").strip().lower()
        self.ai_answer_composer_mode = os.getenv("AI_ANSWER_COMPOSER_MODE", "simple").strip().lower()

        # Backward-compatible aliases for older code and local scripts.
        self.openai_api_key = self.ai_api_key
        self.openai_model = self.ai_model

        self.embedding_provider = os.getenv("EMBEDDING_PROVIDER", "local").strip().lower()
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5").strip()
        self.embedding_api_key = (
            os.getenv("EMBEDDING_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ).strip()
        self.embedding_base_url = (os.getenv("EMBEDDING_BASE_URL") or os.getenv("AI_BASE_URL") or "").strip()
        self.embedding_batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
        self.embedding_max_chars = int(os.getenv("EMBEDDING_MAX_CHARS", "1800"))

        self.crawl_delay_seconds = float(os.getenv("CRAWL_DELAY_SECONDS", "1.0"))
        self.crawl_max_pages_per_site = int(os.getenv("CRAWL_MAX_PAGES_PER_SITE", "200"))
        self.crawl_max_depth = int(os.getenv("CRAWL_MAX_DEPTH", "6"))
        self.crawl_days_back = int(os.getenv("CRAWL_DAYS_BACK", "730"))
        self.extra_seed_sites_json = os.getenv("EXTRA_SEED_SITES_JSON", "").strip()
        db_path = os.getenv("SEU_SEARCH_DB", "backend/data/seu_search.sqlite3")
        self.db_path = (ROOT_DIR / db_path).resolve()
        report_path = os.getenv("SEU_CRAWL_REPORT", "backend/data/crawl_report.json")
        self.crawl_report_path = (ROOT_DIR / report_path).resolve()


settings = Settings()
