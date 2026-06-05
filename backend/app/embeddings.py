from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from functools import lru_cache
from typing import Any

from openai import OpenAI

from .config import settings


HASH_EMBEDDING_DIM = 128
EMBEDDING_SCHEMA_VERSION = 2
DEFAULT_LOCAL_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_API_MODEL = "text-embedding-3-small"
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,24}")


class EmbeddingError(RuntimeError):
    pass


def embedding_provider() -> str:
    provider = (settings.embedding_provider or "hash").strip().lower()
    return provider if provider in {"hash", "local", "api"} else "hash"


def embedding_model() -> str:
    provider = embedding_provider()
    if settings.embedding_model:
        return settings.embedding_model
    if provider == "local":
        return DEFAULT_LOCAL_MODEL
    if provider == "api":
        return DEFAULT_API_MODEL
    return f"hash-{HASH_EMBEDDING_DIM}"


def embedding_signature() -> dict[str, Any]:
    return {
        "provider": embedding_provider(),
        "model": embedding_model(),
        "dim": embedding_dim(),
        "version": EMBEDDING_SCHEMA_VERSION,
    }


def embedding_dim() -> int:
    provider = embedding_provider()
    if provider == "hash":
        return HASH_EMBEDDING_DIM
    if provider == "local":
        return _local_model().get_sentence_embedding_dimension()
    if provider == "api":
        return _api_embedding_dim()
    return HASH_EMBEDDING_DIM


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]


def embed_texts(texts: list[str]) -> list[list[float]]:
    cleaned = [_clean_text(text) for text in texts]
    provider = embedding_provider()
    if provider == "hash":
        return [_hash_embed_text(text) for text in cleaned]
    if provider == "local":
        return _local_embed_texts(cleaned)
    if provider == "api":
        return _api_embed_texts(cleaned)
    return [_hash_embed_text(text) for text in cleaned]


def vector_to_json(vector: list[float]) -> str:
    return json.dumps(vector, separators=(",", ":"))


def embedding_to_json(vector: list[float]) -> str:
    payload = {**embedding_signature(), "vector": vector}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def vector_from_json(value: str | None) -> list[float]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [float(item) for item in data]
    if isinstance(data, dict) and isinstance(data.get("vector"), list):
        return [float(item) for item in data["vector"]]
    return []


def embedding_json_matches_current(value: str | None) -> bool:
    if not value:
        return False
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return False
    if isinstance(data, list):
        return False
    if not isinstance(data, dict):
        return False
    signature = embedding_signature()
    return (
        data.get("provider") == signature["provider"]
        and data.get("model") == signature["model"]
        and int(data.get("dim") or 0) == signature["dim"]
        and int(data.get("version") or 0) == signature["version"]
        and isinstance(data.get("vector"), list)
    )


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    max_chars = max(200, settings.embedding_max_chars)
    return text[:max_chars]


def _hash_embed_text(text: str, dim: int = HASH_EMBEDDING_DIM) -> list[float]:
    vector = [0.0] * dim
    counts = Counter(_hash_tokens(text or ""))
    if not counts:
        return vector
    for token, count in counts.items():
        digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * math.log1p(count)
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 6) for value in vector]


def _hash_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in TOKEN_RE.findall(text or ""):
        if re.fullmatch(r"[\u4e00-\u9fff]{2,}", token):
            if len(token) <= 8:
                tokens.append(token)
            for size in (2, 3, 4):
                if len(token) >= size:
                    tokens.extend(token[index : index + size] for index in range(0, len(token) - size + 1))
        else:
            tokens.append(token.lower())
    for run in CJK_RUN_RE.findall(text or ""):
        if len(run) <= 8:
            tokens.append(run)
        for size in (2, 3, 4):
            if len(run) >= size:
                tokens.extend(run[index : index + size] for index in range(0, len(run) - size + 1))
    return [token for token in tokens if len(token) >= 2]


@lru_cache(maxsize=1)
def _local_model():
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:  # pragma: no cover - optional dependency
        raise EmbeddingError(
            "EMBEDDING_PROVIDER=local requires sentence-transformers. "
            "Install it or switch EMBEDDING_PROVIDER=hash/api."
        ) from exc
    model_name = embedding_model()
    try:
        return SentenceTransformer(model_name, local_files_only=True)
    except Exception as offline_exc:
        try:
            return SentenceTransformer(model_name)
        except Exception as online_exc:
            raise EmbeddingError(
                f"Could not load local embedding model {model_name!r}. "
                "If this is the first run, download the model with network access first."
            ) from online_exc


def _local_embed_texts(texts: list[str]) -> list[list[float]]:
    model = _local_model()
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [[round(float(value), 6) for value in vector] for vector in vectors]


@lru_cache(maxsize=1)
def _api_client() -> OpenAI:
    if not settings.embedding_api_key:
        raise EmbeddingError("EMBEDDING_PROVIDER=api requires EMBEDDING_API_KEY or OPENAI_API_KEY.")
    kwargs: dict[str, str] = {"api_key": settings.embedding_api_key}
    if settings.embedding_base_url:
        kwargs["base_url"] = settings.embedding_base_url
    return OpenAI(**kwargs)


def _api_embed_texts(texts: list[str]) -> list[list[float]]:
    client = _api_client()
    response = client.embeddings.create(model=embedding_model(), input=texts)
    vectors = [item.embedding for item in response.data]
    return [_normalize_vector(vector) for vector in vectors]


@lru_cache(maxsize=1)
def _api_embedding_dim() -> int:
    return len(_api_embed_texts(["dimension probe"])[0])


def _normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(float(value) * float(value) for value in vector)) or 1.0
    return [round(float(value) / norm, 6) for value in vector]
