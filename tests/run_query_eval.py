from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTSET = ROOT / "tests" / "query_testset.jsonl"
OUTPUT_DIR = ROOT / "tests" / "outputs"


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    return cases


def contains_any(text: str, terms: list[str]) -> bool:
    return any(term and term in text for term in terms)


def contains_all_required(text: str, terms: list[str]) -> bool:
    return all(term and term in text for term in terms)


def expand_dynamic_terms(terms: list[str]) -> list[str]:
    today = date.today()
    replacements = {
        "$TODAY": today.isoformat(),
        "${TODAY}": today.isoformat(),
        "$YEAR": str(today.year),
        "${YEAR}": str(today.year),
    }
    expanded: list[str] = []
    for term in terms:
        if not isinstance(term, str):
            continue
        value = term
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, replacement)
        expanded.append(value)
    return expanded


def matched_terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term and term in text]


def missing_terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term and term not in text]


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def top_publish_date_ok(top_date: str | None, min_top_publish_date: str | None, max_top_age_days: int | None) -> bool:
    published = parse_date(top_date)
    if min_top_publish_date:
        minimum = parse_date(min_top_publish_date)
        if not published or (minimum and published < minimum):
            return False
    if max_top_age_days is not None:
        if not published:
            return False
        return (date.today() - published).days <= max_top_age_days
    return True


def judge_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    plan = result.get("query_plan", {})
    hits = result.get("hits", [])
    answer = result.get("answer", {})
    evidence_items = answer.get("evidence") or []
    combined_titles = " ".join(hit.get("title", "") for hit in hits)
    combined_relevance = " ".join(hit.get("relevance_note") or "" for hit in hits)
    combined_evidence_text = " ".join(
        " ".join(
            [
                hit.get("title", "") or "",
                hit.get("snippet", "") or "",
                hit.get("matched_chunk_text", "") or "",
                " ".join(hit.get("keywords") or []),
                " ".join(hit.get("topics") or []),
                " ".join(item.get("name", "") for item in hit.get("attachments") or []),
            ]
        )
        for hit in hits
    )
    combined_answer = answer.get("answer", "")
    combined_urls = " ".join(hit.get("url", "") for hit in hits)
    combined = " ".join([combined_evidence_text, combined_answer, combined_urls])
    combined_with_diagnostics = " ".join([combined_titles, combined_relevance, combined_answer, combined_urls])
    demo_urls = [hit.get("url", "") for hit in hits if "/demo/" in (hit.get("url", "") or "")]

    expected_intent = case.get("expected_intent")
    expected_terms = expand_dynamic_terms(case.get("expected_terms", []))
    required_answer_terms = expand_dynamic_terms(case.get("required_answer_terms", []))
    forbidden_terms = expand_dynamic_terms(case.get("forbidden_terms", []))
    expected_confidence = case.get("expected_confidence")
    expected_top_terms = expand_dynamic_terms(case.get("expected_top_terms", []))
    min_top_publish_date = case.get("min_top_publish_date")
    max_top_age_days = case.get("max_top_age_days")
    top_title = hits[0].get("title", "") if hits else ""
    top_publish_date = hits[0].get("publish_date") if hits else None
    checks = {
        "intent_match": plan.get("intent") == expected_intent,
        "has_sources": bool(hits),
        "has_answer": bool(combined_answer.strip()),
        "expected_terms_present": True if not expected_terms else contains_any(combined, expected_terms),
        "required_answer_terms_present": contains_all_required(combined_answer, required_answer_terms),
        "forbidden_terms_absent": not contains_any(combined, forbidden_terms),
        "confidence_match": True if not expected_confidence else answer.get("confidence") == expected_confidence,
        "top_terms_present": True if not expected_top_terms else contains_all_required(top_title, expected_top_terms),
        "has_relevance_note": all(hit.get("relevance_note") for hit in hits[: min(3, len(hits))]),
        "no_duplicate_titles": len({hit.get("title") for hit in hits}) == len(hits),
        "no_demo_urls": "/demo/" not in combined_with_diagnostics,
        "has_bold_lead_summary": combined_answer.lstrip().startswith("**"),
        "top_publish_date_ok": top_publish_date_ok(top_publish_date, min_top_publish_date, max_top_age_days),
    }
    diagnostics = {
        "top_title": top_title,
        "top_publish_date": top_publish_date,
        "min_top_publish_date": min_top_publish_date,
        "max_top_age_days": max_top_age_days,
        "matched_expected_terms": matched_terms(combined, expected_terms),
        "missing_required_answer_terms": missing_terms(combined_answer, required_answer_terms),
        "found_forbidden_terms": matched_terms(combined, forbidden_terms),
        "missing_top_terms": missing_terms(top_title, expected_top_terms),
        "demo_urls": demo_urls,
        "evidence_judge_status": (result.get("evidence_judge") or {}).get("status"),
        "evidence_judge_notes": (result.get("evidence_judge") or {}).get("notes"),
        "evidence_judge_accepted": (result.get("evidence_judge") or {}).get("accepted") or [],
        "evidence_judge_rejected": (result.get("evidence_judge") or {}).get("rejected") or [],
        "fact_cards": [
            {
                "ref": item.get("ref"),
                "title": item.get("title"),
                "reason": item.get("reason"),
                "quote": item.get("quote"),
                "evidence_type": item.get("evidence_type"),
                "fact_confidence": item.get("fact_confidence"),
            }
            for item in evidence_items[:8]
        ],
    }
    expected_unknown = expected_intent == "unknown"
    if expected_unknown:
        passed = checks["intent_match"] and checks["has_answer"] and checks["has_bold_lead_summary"] and not checks["has_sources"]
    else:
        passed = all(checks.values()) if hits else checks["intent_match"] and checks["has_answer"] and checks["has_bold_lead_summary"]

    return {
        "case_id": case["id"],
        "query": case["query"],
        "expected_intent": expected_intent,
        "actual_intent": plan.get("intent"),
        "output_preset": plan.get("output_preset"),
        "checks": checks,
        "diagnostics": diagnostics,
        "expected_terms": expected_terms,
        "required_answer_terms": required_answer_terms,
        "forbidden_terms": forbidden_terms,
        "expected_top_terms": expected_top_terms,
        "pass": passed,
        "answer_chars": len(combined_answer),
        "confidence": answer.get("confidence"),
        "source_count": len(hits),
        "top_sources": [
            {
                "title": hit.get("title"),
                "source": hit.get("source"),
                "date": hit.get("publish_date"),
                "relevance_note": hit.get("relevance_note"),
                "evidence_judge_label": hit.get("evidence_judge_label"),
                "evidence_judge_confidence": hit.get("evidence_judge_confidence"),
                "evidence_judge_answerable_slots": hit.get("evidence_judge_answerable_slots") or [],
                "evidence_judge_reason": hit.get("evidence_judge_reason"),
                "url": hit.get("url"),
            }
            for hit in hits[:5]
        ],
        "evidence_judge": result.get("evidence_judge"),
        "answer": combined_answer,
        "notes": case.get("notes"),
    }


def search_locally(query: str, profile_payload: dict[str, Any], limit: int) -> dict[str, Any]:
    from backend.app.ai.answerer import Answerer
    from backend.app.ai.planner import QueryPlanner
    from backend.app.ai.evidence_judge import AIEvidenceJudge
    from backend.app.ai.reranker import AIReranker
    from backend.app.config import settings
    from backend.app.models import UserProfile
    from backend.app.search.engine import SearchEngine
    from backend.app.storage import DocumentStore

    store = DocumentStore()
    store.init_db()
    profile = UserProfile(**profile_payload)
    planner = QueryPlanner()
    engine = SearchEngine(store)
    reranker = AIReranker()
    evidence_judge = AIEvidenceJudge()
    answerer = Answerer()

    plan = planner.plan(query, profile)
    if plan.intent == "unknown":
        return {
            "query_plan": plan.model_dump(mode="json"),
            "hits": [],
            "answer": {
                "answer": (
                    "**结论：这个问题不像是在查询学校官网或教务处公开信息，暂不生成官网事实性答案。**\n\n"
                    "可以改成要找的事项、通知名称、时间、学院或年级，我会只基于已收录的官网来源检索。"
                ),
                "confidence": "none",
                "sources": [],
                "evidence_notes": [],
                "evidence": [],
                "warnings": ["非官网事务不进入检索，避免用无关官网内容凑答案。"],
            },
        }
    candidates = engine.search(plan, profile, max(limit * 3, 24))
    candidates, reranker_report = reranker.rerank(query, plan, candidates, profile)
    if reranker_report.notes:
        plan.notes = f"{plan.notes or ''} AI reranker: {reranker_report.notes}".strip()
    if settings.ai_evidence_judge_mode in {"off", "false", "0", "disabled"}:
        from backend.app.ai.evidence_judge import EvidenceJudgeReport

        hits = candidates[:limit]
        judge_report = EvidenceJudgeReport(
            status="skipped",
            notes="Evidence Judge 已关闭：本地检索结果直接交给 Fact Reader 总结。",
            candidate_count=min(len(candidates), limit),
        )
    else:
        hits, judge_report = evidence_judge.judge(query, plan, candidates, profile, limit)
    hits = hits[:limit]
    if judge_report.notes:
        plan.notes = f"{plan.notes or ''} AI evidence judge: {judge_report.notes}".strip()
    answer = answerer.answer(query, plan, hits)
    return {
        "query_plan": plan.model_dump(mode="json"),
        "hits": [hit.model_dump(mode="json") for hit in hits],
        "answer": answer.model_dump(mode="json"),
        "evidence_judge": judge_report.model_dump(mode="json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--mode", choices=["api", "local"], default="api")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = load_cases(TESTSET)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"query_eval_{timestamp}.json"
    summary_path = OUTPUT_DIR / "latest_summary.md"

    client = httpx.Client(timeout=args.timeout)
    records = []
    for index, case in enumerate(cases, start=1):
        payload = {
            "query": case["query"],
            "profile": case.get("profile") or {},
            "limit": args.limit,
        }
        started = time.time()
        try:
            if args.mode == "local":
                result = search_locally(case["query"], case.get("profile") or {}, args.limit)
            else:
                response = client.post(f"{args.base_url}/api/search", json=payload)
                response.raise_for_status()
                result = response.json()
            record = judge_case(case, result)
            record["latency_seconds"] = round(time.time() - started, 3)
            print(f"[{index:02d}/{len(cases)}] {case['id']} pass={record['pass']} intent={record['actual_intent']}")
        except Exception as exc:
            record = {
                "case_id": case["id"],
                "query": case["query"],
                "pass": False,
                "error": str(exc),
                "latency_seconds": round(time.time() - started, 3),
            }
            print(f"[{index:02d}/{len(cases)}] {case['id']} ERROR {exc}")
        records.append(record)
        time.sleep(args.sleep)

    output = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "case_count": len(records),
        "pass_count": sum(1 for item in records if item.get("pass")),
        "records": records,
    }
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Query Eval {output['created_at']}",
        "",
        f"- Cases: {output['case_count']}",
        f"- Passed: {output['pass_count']}",
        f"- Output: `{output_path.name}`",
        "",
        "## Failures",
    ]
    failures = [item for item in records if not item.get("pass")]
    if not failures:
        lines.append("- None")
    for item in failures:
        diagnostics = item.get("diagnostics") or {}
        lines.append(
            f"- `{item['case_id']}` {item['query']} | intent {item.get('actual_intent')} "
            f"| checks {item.get('checks')} | error {item.get('error')}"
        )
        if diagnostics:
            lines.append(
                f"  - top=`{diagnostics.get('top_title') or '-'}` "
                f"| missing_required={diagnostics.get('missing_required_answer_terms') or []} "
                f"| forbidden_found={diagnostics.get('found_forbidden_terms') or []} "
                f"| demo_urls={diagnostics.get('demo_urls') or []}"
            )
    lines.append("")
    lines.append("## Samples")
    for item in records[:8]:
        top_title = item.get("top_sources", [{}])[0].get("title") if item.get("top_sources") else None
        lines.append(f"- `{item['case_id']}` pass={item.get('pass')} top={top_title}")
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"saved {output_path}")
    print(f"summary {summary_path}")


if __name__ == "__main__":
    main()
