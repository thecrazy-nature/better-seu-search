from __future__ import annotations

from openai import OpenAI

from ..config import settings


class AIUnavailableError(RuntimeError):
    """Raised when an AI-required runtime path cannot call the AI service."""


def make_ai_client() -> OpenAI | None:
    if not settings.ai_api_key:
        return None
    kwargs = {
        "api_key": settings.ai_api_key,
        "timeout": settings.ai_timeout_seconds,
        "max_retries": settings.ai_max_retries,
    }
    if settings.ai_base_url:
        kwargs["base_url"] = settings.ai_base_url
    return OpenAI(**kwargs)
