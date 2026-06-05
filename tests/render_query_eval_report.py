from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "tests" / "outputs"
DEFAULT_REPORT = OUTPUT_DIR / "query_eval_report.md"


def latest_eval_file() -> Path:
    files = sorted(OUTPUT_DIR.glob("query_eval_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No query_eval_*.json files found in {OUTPUT_DIR}")
    return files[0]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def short_text(text: str, limit: int = 180) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def table_escape(value: object) -> str:
    text = str(value or "")
    return text.replace("|", "\\|").replace("\n", " ")


def check_summary(checks: dict[str, bool] | None, passed: bool | None = None) -> str:
    if not checks:
        return ""
    failed = [name for name, ok in checks.items() if not ok]
    if not failed:
        return "全部满足"
    prefix = "通过判定；提示：" if passed else "失败："
    return prefix + "、".join(failed)


def failed_check_names(checks: dict[str, bool] | None) -> str:
    if not checks:
        return "-"
    failed = [name for name, ok in checks.items() if not ok]
    return "、".join(failed) if failed else "无"


def diagnostic_summary(item: dict[str, Any]) -> str:
    diagnostics = item.get("diagnostics") or {}
    parts: list[str] = []
    missing_required = diagnostics.get("missing_required_answer_terms") or []
    forbidden_found = diagnostics.get("found_forbidden_terms") or []
    missing_top = diagnostics.get("missing_top_terms") or []
    demo_urls = diagnostics.get("demo_urls") or []
    top_date = diagnostics.get("top_publish_date")
    min_top_date = diagnostics.get("min_top_publish_date")
    max_top_age = diagnostics.get("max_top_age_days")
    if missing_required:
        parts.append(f"答案缺：{', '.join(missing_required)}")
    if forbidden_found:
        parts.append(f"误命中：{', '.join(forbidden_found)}")
    if missing_top:
        parts.append(f"Top 标题缺：{', '.join(missing_top)}")
    if min_top_date or max_top_age is not None:
        requirement = []
        if min_top_date:
            requirement.append(f"不早于 {min_top_date}")
        if max_top_age is not None:
            requirement.append(f"{max_top_age} 天内")
        parts.append(f"Top 发布时间：{top_date or '-'}（要求：{', '.join(requirement)}）")
    if demo_urls:
        parts.append(f"Demo URL：{', '.join(demo_urls)}")
    return "；".join(parts) if parts else "-"


def render_diagnostics(item: dict[str, Any]) -> list[str]:
    diagnostics = item.get("diagnostics") or {}
    if not diagnostics:
        return []
    lines = [
        f"- Top 标题：{diagnostics.get('top_title') or '-'}",
        f"- Top 发布时间：{diagnostics.get('top_publish_date') or '-'}",
        f"- 已命中期望词：{', '.join(diagnostics.get('matched_expected_terms') or []) or '-'}",
        f"- 答案缺失必备词：{', '.join(diagnostics.get('missing_required_answer_terms') or []) or '-'}",
        f"- 误命中禁用词：{', '.join(diagnostics.get('found_forbidden_terms') or []) or '-'}",
        f"- Top 标题缺词：{', '.join(diagnostics.get('missing_top_terms') or []) or '-'}",
        f"- Top 发布时间要求：不早于 {diagnostics.get('min_top_publish_date') or '-'}，"
        f"不超过 {diagnostics.get('max_top_age_days') if diagnostics.get('max_top_age_days') is not None else '-'} 天",
        f"- Demo URL：{', '.join(diagnostics.get('demo_urls') or []) or '-'}",
    ]
    if diagnostics.get("evidence_judge_status"):
        lines.append(f"- Evidence Judge：{diagnostics.get('evidence_judge_status')}；{diagnostics.get('evidence_judge_notes') or '-'}")
    accepted = diagnostics.get("evidence_judge_accepted") or []
    rejected = diagnostics.get("evidence_judge_rejected") or []
    if accepted:
        lines.append("- Judge 保留：")
        for item in accepted[:3]:
            slots = ", ".join(item.get("answerable_slots") or []) or "-"
            lines.append(
                f"  - {item.get('label')}({item.get('confidence')}) {item.get('title')}；槽位：{slots}；原因：{item.get('reason') or '-'}"
            )
    if rejected:
        lines.append("- Judge 剔除：")
        for item in rejected[:5]:
            slots = ", ".join(item.get("answerable_slots") or []) or "-"
            lines.append(
                f"  - {item.get('label')}({item.get('confidence')}) {item.get('title')}；槽位：{slots}；原因：{item.get('reason') or '-'}"
            )
    fact_cards = diagnostics.get("fact_cards") or []
    if fact_cards:
        lines.append("- 事实卡片：")
        for item in fact_cards[:5]:
            quote = short_text(item.get("quote") or "", 90)
            lines.append(
                f"  - {item.get('ref') or '-'} {item.get('evidence_type') or '-'}"
                f"/{item.get('fact_confidence') if item.get('fact_confidence') is not None else '-'}："
                f"{item.get('reason') or '-'}；原文：{quote or '-'}"
            )
    return lines


def case_group(case_id: str) -> str:
    prefix = case_id.split("_", 1)[0]
    mapping = {
        "deadline": "时间/截止",
        "find": "原文/附件查找",
        "attachment": "附件查找",
        "eligibility": "资格/适用对象",
        "process": "流程/办理",
        "answer": "综合问答",
        "latest": "最新通知",
        "profile": "画像相关",
        "ambiguous": "模糊问题",
        "unknown": "非官网问题",
        "noise": "抗噪声",
        "compare": "对比问题",
        "exclude": "排除条件",
        "offschool": "外校/越界",
    }
    return mapping.get(prefix, "其他")


def result_text(item: dict[str, Any]) -> str:
    return "通过" if item.get("pass") else "失败"


def source_summary(source: dict[str, Any]) -> str:
    parts = [str(source.get("title") or "未命名来源")]
    meta = " / ".join(str(value) for value in (source.get("source"), source.get("date")) if value)
    if meta:
        parts.append(f"（{meta}）")
    if source.get("url"):
        parts.append(f"\n  {source.get('url')}")
    if source.get("relevance_note"):
        parts.append(f"\n  相关性：{source.get('relevance_note')}")
    if source.get("evidence_judge_label"):
        slots = ", ".join(source.get("evidence_judge_answerable_slots") or []) or "-"
        parts.append(
            f"\n  Judge：{source.get('evidence_judge_label')} / {source.get('evidence_judge_confidence')} / 槽位：{slots}"
        )
    return "".join(parts)


def render_case_detail(index: int, item: dict[str, Any]) -> list[str]:
    lines = [
        f"### {index}. {item.get('case_id')} - {result_text(item)}",
        "",
        f"- 问题：{item.get('query')}",
        f"- 意图：期望 `{item.get('expected_intent')}`，实际 `{item.get('actual_intent')}`",
        f"- 置信度：`{item.get('confidence')}`",
        f"- 来源数：{item.get('source_count', 0)}",
        f"- 延迟：{item.get('latency_seconds', '-')}s",
        f"- 检查项：{check_summary(item.get('checks'), item.get('pass'))}",
    ]
    if item.get("notes"):
        lines.append(f"- 备注：{item.get('notes')}")
    if item.get("error"):
        lines.append(f"- 错误：{item.get('error')}")
    diagnostic_lines = render_diagnostics(item)
    if diagnostic_lines:
        lines.extend(["", "诊断："])
        lines.extend(diagnostic_lines)

    lines.extend(["", "Top 来源："])
    sources = item.get("top_sources") or []
    if not sources:
        lines.append("- 无")
    for source_index, source in enumerate(sources[:3], start=1):
        lines.append(f"{source_index}. {source_summary(source)}")

    answer = short_text(item.get("answer", ""), 700)
    lines.extend(["", "答案摘要：", "", "```text", answer or "（无答案）", "```", ""])
    return lines


def render_report(data: dict[str, Any], source_path: Path) -> str:
    records = data.get("records", [])
    pass_count = sum(1 for item in records if item.get("pass"))
    latencies = [float(item["latency_seconds"]) for item in records if "latency_seconds" in item]
    failures = [item for item in records if not item.get("pass")]
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(case_group(record.get("case_id", "")), []).append(record)

    lines: list[str] = [
        "# 官网检索测试报告",
        "",
        "## 总览",
        "",
        f"- 评测时间：{data.get('created_at', '-')}",
        f"- 评测文件：`{source_path.name}`",
        f"- 用例总数：{len(records)}",
        f"- 通过数量：{pass_count}",
        f"- 通过率：{pass_count / len(records) * 100:.1f}%" if records else "- 通过率：-",
    ]
    if latencies:
        p95_index = max(0, min(len(latencies) - 1, int(len(latencies) * 0.95) - 1))
        sorted_latencies = sorted(latencies)
        lines.extend(
            [
                f"- 平均延迟：{statistics.mean(latencies):.3f}s",
                f"- 中位延迟：{statistics.median(latencies):.3f}s",
                f"- P95 延迟：{sorted_latencies[p95_index]:.3f}s",
                f"- 最大延迟：{max(latencies):.3f}s",
            ]
        )

    lines.extend(["", "## 分类结果", ""])
    for group, items in sorted(groups.items()):
        passed = sum(1 for item in items if item.get("pass"))
        failed = len(items) - passed
        lines.append(f"- {group}：{passed}/{len(items)} 通过，失败 {failed}")

    lines.extend(["", "## 失败用例", ""])
    if not failures:
        lines.append("- 无")
    else:
        lines.extend(
            [
                "| 用例 | 问题 | 失败项 | 关键诊断 | Top 来源 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in failures:
            diagnostics = item.get("diagnostics") or {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        table_escape(item.get("case_id")),
                        table_escape(item.get("query")),
                        table_escape(failed_check_names(item.get("checks"))),
                        table_escape(diagnostic_summary(item)),
                        table_escape(diagnostics.get("top_title") or "-"),
                    ]
                )
                + " |"
            )
        lines.append("")
        for item in failures:
            lines.extend(
                [
                    f"### {item.get('case_id')}：{item.get('query')}",
                    "",
                    f"- 期望意图：`{item.get('expected_intent')}`",
                    f"- 实际意图：`{item.get('actual_intent')}`",
                    f"- 置信度：`{item.get('confidence')}`",
                    f"- 检查项：{check_summary(item.get('checks'), item.get('pass'))}",
                    f"- 错误：{item.get('error') or '-'}",
                    "",
                    "诊断：",
                    *render_diagnostics(item),
                    "",
                    "Top 来源：",
                ]
            )
            for source in item.get("top_sources", [])[:5]:
                lines.append(
                    f"- {source.get('title')} | {source.get('source')} | {source.get('date') or '-'} | {source.get('url') or '-'}"
                )
            lines.extend(["", "答案摘要：", "", f"> {short_text(item.get('answer', ''), 500)}", ""])

    lines.extend(["", "## 用例索引", ""])
    for index, item in enumerate(records, start=1):
        lines.append(
            f"- {index}. `{item.get('case_id')}` {result_text(item)}，"
            f"意图 `{item.get('actual_intent')}`，延迟 {item.get('latency_seconds', '-')}s"
        )
        lines.append(f"  问题：{item.get('query')}")

    lines.extend(["", "## 全量用例详情", ""])
    for index, item in enumerate(records, start=1):
        lines.extend(render_case_detail(index, item))

    lines.extend(["", "## 检查项说明", ""])
    lines.extend(
        [
            "- `intent_match`：实际意图是否符合期望。",
            "- `has_sources`：是否返回来源。",
            "- `has_answer`：是否生成答案。",
            "- `expected_terms_present`：标题、摘要、命中文本、关键词、附件名、答案或 URL 中是否出现期望词；不再把系统自写的相关性说明当作证据。",
            "- `required_answer_terms_present`：答案中是否包含必须出现的词。",
            "- `forbidden_terms_absent`：标题、相关性说明和答案中是否避开禁用词。",
            "- `confidence_match`：答案置信度是否符合期望。",
            "- `top_terms_present`：Top 来源标题是否包含期望词。",
            "- `has_relevance_note`：Top 来源是否有相关性说明。",
            "- `no_duplicate_titles`：结果是否没有重复标题。",
            "- `no_demo_urls`：标题、相关性说明、答案和 URL 中是否没有演示数据 `/demo/`。",
            "- `has_bold_lead_summary`：答案首行是否有 `**...**` 加粗的直接结论。",
            "- `top_publish_date_ok`：Top 来源发布时间是否满足用例要求，例如“最近”问题不得返回过旧来源。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=None, help="Path to query_eval_*.json. Defaults to latest.")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    source_path = args.input or latest_eval_file()
    data = load_json(source_path)
    report = render_report(data, source_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print({"input": str(source_path), "output": str(args.output), "cases": data.get("case_count"), "passed": data.get("pass_count")})


if __name__ == "__main__":
    main()
