from __future__ import annotations

import ipaddress
import json
import re
import socket
import traceback
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..ai.answerer import Answerer
from ..ai.evidence_judge import AIEvidenceJudge
from ..ai.evidence_judge import EvidenceJudgeReport
from ..ai.planner import QueryPlanner
from ..config import ROOT_DIR
from ..config import settings
from ..crawl import run_crawl
from ..models import (
    AnswerResult,
    CollectionDetail,
    CollectionSourceConfig,
    CollectionSummary,
    QueryPlan,
    SearchHit,
    UserProfile,
)
from ..search.engine import SearchEngine
from ..storage import DocumentStore


FRONTEND_DIR = ROOT_DIR / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"
LOCAL_ONLY_MESSAGE = "This endpoint is available only from the local machine."


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    profile: UserProfile = Field(default_factory=UserProfile)
    limit: int = Field(default=8, ge=1, le=20)
    session_id: str | None = None
    collection_id: int | None = Field(default=None, ge=1)


class SearchResponse(BaseModel):
    query_plan: QueryPlan
    hits: list[SearchHit]
    answer: AnswerResult
    evidence_judge: EvidenceJudgeReport | None = None
    session_id: str | None = None
    collection_id: int | None = None
    collection_name: str | None = None


class CrawlResponse(BaseModel):
    upserted: int
    total_documents: int


class CrawlTaskResponse(BaseModel):
    task_id: str
    status: str


class CollectionUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=400)
    is_enabled: bool = True


class CollectionSourceUpsertRequest(BaseModel):
    source_name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=300)
    seed_urls: list[str] = Field(default_factory=list)
    include_path_prefixes: list[str] = Field(default_factory=list)
    exclude_path_prefixes: list[str] = Field(default_factory=list)
    max_depth: int | None = Field(default=None, ge=0, le=8)
    max_pages: int | None = Field(default=None, ge=1, le=5000)
    days_back: int | None = Field(default=None, ge=1, le=3650)
    is_enabled: bool = True


class ClientAccess(BaseModel):
    is_local_client: bool
    can_search: bool = True
    can_manage_index: bool = False


store = DocumentStore()
planner = QueryPlanner()
engine = SearchEngine(store)
answerer = Answerer()
evidence_judge = AIEvidenceJudge()
SEARCH_CACHE_MAX = 128
search_cache: OrderedDict[str, SearchResponse] = OrderedDict()
crawl_tasks: dict[str, dict] = {}
search_sessions: dict[str, dict] = {}


def _normalize_host(host: str | None) -> str:
    if not host:
        return ""
    normalized = host.strip().lower().strip("[]")
    if "%" in normalized:
        normalized = normalized.split("%", 1)[0]
    return normalized


def _discover_local_client_hosts() -> set[str]:
    hosts = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}
    hostnames = {socket.gethostname(), socket.getfqdn(), "localhost"}

    for hostname in hostnames:
        if not hostname:
            continue
        hosts.add(hostname)
        try:
            resolved = socket.gethostbyname_ex(hostname)[2]
        except OSError:
            resolved = []
        for value in resolved:
            hosts.add(value)
            hosts.add(f"::ffff:{value}")
        try:
            infos = socket.getaddrinfo(hostname, None)
        except OSError:
            infos = []
        for _, _, _, _, sockaddr in infos:
            if not sockaddr:
                continue
            value = sockaddr[0]
            hosts.add(value)
            if ":" not in value:
                hosts.add(f"::ffff:{value}")

    return {_normalize_host(value) for value in hosts if value}


LOCAL_CLIENT_HOSTS = _discover_local_client_hosts()


def _is_local_host(host: str | None) -> bool:
    normalized = _normalize_host(host)
    if not normalized:
        return False
    if normalized in LOCAL_CLIENT_HOSTS:
        return True
    if normalized.startswith("::ffff:"):
        return _is_local_host(normalized.removeprefix("::ffff:"))
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return normalized == "localhost"


def _is_local_request(request: Request) -> bool:
    client = request.client
    return _is_local_host(client.host if client else None)


def _client_access(request: Request) -> ClientAccess:
    is_local_client = _is_local_request(request)
    return ClientAccess(
        is_local_client=is_local_client,
        can_manage_index=is_local_client,
    )


def _require_local_request(request: Request) -> None:
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail=LOCAL_ONLY_MESSAGE)


def _collection_summary(collection: dict) -> CollectionSummary:
    return CollectionSummary(**collection)


def _collection_detail(collection: dict) -> CollectionDetail:
    sources = [CollectionSourceConfig(**item) for item in store.list_collection_sources(collection["id"])]
    return CollectionDetail(**collection, sources=sources)


def _resolve_search_collection(collection_id: int | None) -> dict:
    if collection_id is not None:
        collection = store.get_collection(collection_id)
        if collection is None or not collection.get("is_enabled", False):
            raise HTTPException(status_code=404, detail="Collection not found or disabled.")
        return collection

    collection = store.get_default_collection(enabled_only=True)
    if collection is not None:
        return collection

    enabled = store.list_collections(enabled_only=True)
    if not enabled:
        raise HTTPException(status_code=400, detail="No enabled collections are available.")
    return enabled[0]

app = FastAPI(title="SEU Official Search MVP", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def startup() -> None:
    store.init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/admin/collections")
def collections_admin(request: Request) -> FileResponse:
    _require_local_request(request)
    return FileResponse(FRONTEND_DIR / "admin-collections.html")


@app.get("/api/health")
def health(request: Request) -> dict:
    store.init_db()
    return {
        "ok": True,
        **store.get_index_stats(),
        "collections": len(store.list_collections(enabled_only=True)),
        "access": _client_access(request).model_dump(),
    }


@app.get("/api/collections")
def collections() -> dict:
    store.init_db()
    return {
        "collections": [_collection_summary(item).model_dump() for item in store.list_collections(enabled_only=True)]
    }


@app.get("/api/admin/collections")
def admin_collections(request: Request) -> dict:
    _require_local_request(request)
    store.init_db()
    return {"collections": [_collection_detail(item).model_dump() for item in store.list_collections()]}


@app.post("/api/admin/collections")
def create_collection(payload: CollectionUpsertRequest, request: Request) -> dict:
    _require_local_request(request)
    store.init_db()
    collection = store.create_collection(
        name=payload.name,
        description=payload.description,
        is_enabled=payload.is_enabled,
    )
    search_cache.clear()
    return _collection_detail(collection).model_dump()


@app.put("/api/admin/collections/{collection_id}")
def update_collection(collection_id: int, payload: CollectionUpsertRequest, request: Request) -> dict:
    _require_local_request(request)
    store.init_db()
    collection = store.update_collection(
        collection_id,
        name=payload.name,
        description=payload.description,
        is_enabled=payload.is_enabled,
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found.")
    search_cache.clear()
    return _collection_detail(collection).model_dump()


@app.delete("/api/admin/collections/{collection_id}")
def delete_collection(collection_id: int, request: Request) -> dict:
    _require_local_request(request)
    store.init_db()
    try:
        deleted = store.delete_collection(collection_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Collection not found.")
    search_cache.clear()
    return {"ok": True}


@app.post("/api/admin/collections/{collection_id}/sources")
def create_collection_source(collection_id: int, payload: CollectionSourceUpsertRequest, request: Request) -> dict:
    _require_local_request(request)
    store.init_db()
    if store.get_collection(collection_id) is None:
        raise HTTPException(status_code=404, detail="Collection not found.")
    try:
        source = store.create_collection_source(
            collection_id,
            source_name=payload.source_name,
            base_url=payload.base_url,
            seed_urls=payload.seed_urls,
            include_path_prefixes=payload.include_path_prefixes,
            exclude_path_prefixes=payload.exclude_path_prefixes,
            max_depth=payload.max_depth,
            max_pages=payload.max_pages,
            days_back=payload.days_back,
            is_enabled=payload.is_enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    search_cache.clear()
    return CollectionSourceConfig(**source).model_dump()


@app.put("/api/admin/collection-sources/{source_id}")
def update_collection_source(source_id: int, payload: CollectionSourceUpsertRequest, request: Request) -> dict:
    _require_local_request(request)
    store.init_db()
    try:
        source = store.update_collection_source(
            source_id,
            source_name=payload.source_name,
            base_url=payload.base_url,
            seed_urls=payload.seed_urls,
            include_path_prefixes=payload.include_path_prefixes,
            exclude_path_prefixes=payload.exclude_path_prefixes,
            max_depth=payload.max_depth,
            max_pages=payload.max_pages,
            days_back=payload.days_back,
            is_enabled=payload.is_enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if source is None:
        raise HTTPException(status_code=404, detail="Collection source not found.")
    search_cache.clear()
    return CollectionSourceConfig(**source).model_dump()


@app.delete("/api/admin/collection-sources/{source_id}")
def delete_collection_source(source_id: int, request: Request) -> dict:
    _require_local_request(request)
    store.init_db()
    if not store.delete_collection_source(source_id):
        raise HTTPException(status_code=404, detail="Collection source not found.")
    search_cache.clear()
    return {"ok": True}


@app.post("/api/search", response_model=SearchResponse)
def search(payload: SearchRequest) -> SearchResponse:
    store.init_db()
    collection = _resolve_search_collection(payload.collection_id)
    cache_key = _search_cache_key(payload, collection)
    cached = search_cache.get(cache_key)
    if cached:
        search_cache.move_to_end(cache_key)
        return cached.model_copy(deep=True)
    response = _run_search(payload, collection)
    search_cache[cache_key] = response.model_copy(deep=True)
    if len(search_cache) > SEARCH_CACHE_MAX:
        search_cache.popitem(last=False)
    return response


def _run_search(payload: SearchRequest, collection: dict) -> SearchResponse:
    session_id = payload.session_id or uuid.uuid4().hex
    effective_query = _contextualize_query(payload.query, session_id)
    plan = planner.plan(effective_query, payload.profile)
    if plan.intent == "unknown":
        answer = AnswerResult(
            answer=(
                "**结论：这个问题不像是在查询学校官网或教务处公开信息，暂不生成官网事实性答案。**\n\n"
                "可以改成要找的事项、通知名称、时间、学院或年级，我会只基于已收录的官网来源检索。"
            ),
            confidence="none",
            sources=[],
            evidence_notes=[],
            evidence=[],
            warnings=["非官网事务不进入检索，避免用无关官网内容凑答案。"],
        )
        response = SearchResponse(
            query_plan=plan,
            hits=[],
            answer=answer,
            evidence_judge=None,
            session_id=session_id,
            collection_id=collection["id"],
            collection_name=collection["name"],
        )
        _remember_session(session_id, payload.query, plan, payload.profile)
        return response
    candidates = engine.search(
        plan,
        payload.profile,
        max(payload.limit * 3, 24),
        collection_id=collection["id"],
    )
    hits, judge_report = evidence_judge.judge(payload.query, plan, candidates, payload.profile, payload.limit)
    hits = hits[: payload.limit]
    if judge_report.notes:
        plan.notes = f"{plan.notes or ''} AI evidence judge: {judge_report.notes}".strip()
    answer = answerer.answer(payload.query, plan, hits)
    response = SearchResponse(
        query_plan=plan,
        hits=hits,
        answer=answer,
        evidence_judge=judge_report,
        session_id=session_id,
        collection_id=collection["id"],
        collection_name=collection["name"],
    )
    _remember_session(session_id, payload.query, plan, payload.profile)
    return response


def _contextualize_query(query: str, session_id: str) -> str:
    previous = search_sessions.get(session_id)
    if not previous:
        return query
    if not re.search(r"^(那|那么|这个|这个呢|那.*呢|研究生呢|本科生呢|计算机学院呢|还有呢)", query.strip()):
        return query
    plan = previous.get("query_plan")
    if not isinstance(plan, dict):
        return query
    entities = plan.get("entities") or {}
    topic = entities.get("topic")
    action = entities.get("action")
    parts = [item for item in [topic, action, query] if isinstance(item, str) and item]
    return " ".join(parts) if parts else query


def _remember_session(session_id: str, query: str, plan: QueryPlan, profile: UserProfile) -> None:
    search_sessions[session_id] = {
        "query": query,
        "query_plan": plan.model_dump(mode="json"),
        "profile": profile.model_dump(mode="json", exclude_none=True),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if len(search_sessions) > 200:
        oldest = sorted(search_sessions.items(), key=lambda item: item[1].get("updated_at", ""))[:50]
        for key, _ in oldest:
            search_sessions.pop(key, None)


def _search_cache_key(payload: SearchRequest, collection: dict) -> str:
    return json.dumps(
        {
            "query": payload.query.strip(),
            "profile": payload.profile.model_dump(exclude_none=True),
            "limit": payload.limit,
            "collection_id": collection["id"],
            "session_id": payload.session_id,
            "session_context": search_sessions.get(payload.session_id or "", {}).get("query"),
            "index_stats": store.get_index_stats(collection["id"]),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


@app.post("/api/crawl", response_model=CrawlTaskResponse)
def crawl(background_tasks: BackgroundTasks, request: Request) -> CrawlTaskResponse:
    _require_local_request(request)
    store.init_db()
    collection = store.get_default_collection()
    if collection is None:
        raise HTTPException(status_code=400, detail="No collection is available.")
    return _queue_crawl_task(background_tasks, collection["id"])


@app.post("/api/collections/{collection_id}/crawl", response_model=CrawlTaskResponse)
def crawl_collection(collection_id: int, background_tasks: BackgroundTasks, request: Request) -> CrawlTaskResponse:
    _require_local_request(request)
    store.init_db()
    collection = store.get_collection(collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found.")
    return _queue_crawl_task(background_tasks, collection["id"])


def _queue_crawl_task(background_tasks: BackgroundTasks, collection_id: int) -> CrawlTaskResponse:
    task_id = uuid.uuid4().hex
    task = {
        "task_id": task_id,
        "collection_id": collection_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    crawl_tasks[task_id] = task
    store.upsert_crawl_task(task)
    background_tasks.add_task(_run_crawl_task, task_id)
    return CrawlTaskResponse(task_id=task_id, status="queued")


@app.get("/api/crawl/tasks/{task_id}")
def crawl_task(task_id: str, request: Request) -> dict:
    _require_local_request(request)
    task = crawl_tasks.get(task_id) or store.get_crawl_task(task_id)
    if not task:
        return {"error": "not_found"}
    return task


def _run_crawl_task(task_id: str) -> None:
    task = crawl_tasks[task_id]
    task["status"] = "running"
    task["updated_at"] = datetime.now().isoformat(timespec="seconds")
    store.upsert_crawl_task(task)
    try:
        collection_id = task.get("collection_id")
        upserted = run_crawl(collection_id)
        search_cache.clear()
        task.update(
            {
                "status": "completed",
                "upserted": upserted,
                "total_documents": store.count_documents(collection_id if isinstance(collection_id, int) else None),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        store.upsert_crawl_task(task)
    except Exception as exc:
        task.update(
            {
                "status": "failed",
                "error": str(exc),
                "traceback": traceback.format_exc()[-2000:],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        store.upsert_crawl_task(task)


@app.get("/api/crawl/report")
def crawl_report(request: Request) -> dict:
    _require_local_request(request)
    if not settings.crawl_report_path.exists():
        return {"exists": False, "message": "No crawl report generated yet."}
    return {
        "exists": True,
        "path": str(settings.crawl_report_path),
        "report": json.loads(settings.crawl_report_path.read_text(encoding="utf-8")),
    }


@app.get("/api/documents/{doc_id}")
def document(doc_id: int, request: Request) -> dict:
    _require_local_request(request)
    row = store.get_document(doc_id)
    if not row:
        return {"error": "not_found"}
    return dict(row)
