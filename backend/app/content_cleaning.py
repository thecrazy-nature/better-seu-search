from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup


STRICT_ARTICLE_SELECTORS = [
    ".wp_articlecontent",
    "#wp_articlecontent",
    ".arti_content",
    ".article_content",
    ".article-content",
    ".v_news_content",
    ".news_content",
    ".news-con",
    ".news_con",
    ".content-detail",
    ".detail_content",
    ".TRS_Editor",
    "[id*=wp_articlecontent]",
    "[class*=wp_articlecontent]",
]

FALLBACK_ARTICLE_SELECTORS = [
    "article",
    ".article",
    ".main_content",
    ".main-content",
    ".article-main",
    ".article_body",
    ".article-body",
    ".detail",
    ".detail-box",
    ".wp_entry",
    ".wp_article",
    ".content",
    "#content",
    ".main",
]

BAD_ELEMENT_SELECTORS = [
    "script",
    "style",
    "noscript",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "iframe",
    ".nav",
    ".navbar",
    ".menu",
    ".submenu",
    ".footer",
    ".header",
    ".breadcrumb",
    ".location",
    ".position",
    ".share",
    ".search",
    ".sider",
    ".sidebar",
    ".leftmenu",
    ".rightmenu",
    ".wp_nav",
    ".wp_menu",
    "#nav",
    "#footer",
    "#header",
]

FOOTER_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        r"版权所有",
        r"\[?网站管理\]?",
        r"技术支持",
        r"更多联系方式",
        r"处室电话[:：].*更多联系方式",
    ]
]

DATE_LINE_RE = re.compile(r"^(?:发布时间[:：]?\s*)?20\d{2}[-./年]\d{1,2}[-./月]\d{1,2}(?:日)?$")

NAV_LINE_EXACT = {
    "English",
    "简体中文",
    "部门简介",
    "领导分工",
    "处办公室",
    "招生办公室",
    "教学研究科",
    "质量评估科",
    "教务科",
    "学籍管理科",
    "实践教学科",
    "丁家桥教务办",
    "公共教室管理",
    "文化素质教育",
    "教学服务中心",
    "联系我们",
    "办事平台",
    "信息通知",
    "教务信息",
    "学籍管理",
    "导师风采",
    "教学研究",
    "实践教学",
    "教师教学发展",
    "审核评估",
    "评估信息",
    "管理规定",
    "教务管理",
    "毕业设计",
    "实习实践",
    "课外研学",
    "校企合作",
    "办事流程",
    "教务流程",
    "学籍流程",
    "教研流程",
    "实践流程",
    "出国交流",
    "教室借用",
    "首页",
    "最新动态",
    "无图信息",
    "有图信息",
    "办公室信息管理",
}
NAV_LINE_COMPACT = {re.sub(r"\s+", "", item) for item in NAV_LINE_EXACT}

NAV_TERMS = [
    "部门简介",
    "本科生人才培",
    "领导分工",
    "招生办公室",
    "教学研究科",
    "质量评估科",
    "教务科",
    "学籍管理科",
    "实践教学科",
    "公共教室管理",
    "教学服务中心",
    "办事平台",
    "信息通知",
    "教务信息",
    "导师风采",
    "教师教学发展",
    "审核评估",
    "管理规定",
    "毕业设计",
    "实习实践",
    "课外研学",
    "校企合作",
    "办事流程",
    "教务流程",
    "学籍流程",
    "教研流程",
]


def extract_main_text(soup: BeautifulSoup, title: str = "", max_chars: int = 30000) -> str:
    working = BeautifulSoup(str(soup), "html.parser")
    _remove_bad_elements(working)

    candidates: list[tuple[int, str]] = []
    for weight, selector_group in ((100, STRICT_ARTICLE_SELECTORS), (20, FALLBACK_ARTICLE_SELECTORS)):
        for selector in selector_group:
            for node in working.select(selector):
                text = _text_from_node(node)
                cleaned = clean_body_text(text, title=title, max_chars=max_chars)
                if len(cleaned) < 30:
                    continue
                candidates.append((_score_candidate(cleaned, weight), cleaned))

    if candidates:
        return max(candidates, key=lambda item: item[0])[1][:max_chars].strip()

    body = working.select_one("body") or working
    return clean_body_text(_text_from_node(body), title=title, max_chars=max_chars)


def clean_body_text(text: str, title: str = "", max_chars: int = 30000) -> str:
    text = strip_embedded_attachment_text(text)
    lines = _normalize_lines(text)
    lines = _trim_before_title(lines, title)
    lines = _drop_leading_title_and_date(lines, title)
    lines = _truncate_footer(lines)
    lines = _drop_noise_lines(lines)
    lines = _dedupe_nearby_lines(lines)
    lines = _merge_fragment_lines(lines)
    lines = _drop_template_only_body(lines)
    return "\n".join(lines).strip()[:max_chars]


def strip_embedded_attachment_text(text: str) -> str:
    return re.split(r"\n\s*(?:附件正文摘录[:：]|附件《.+》正文摘录[:：])", text or "", maxsplit=1)[0]


def boilerplate_score(text: str) -> int:
    normalized = _normalize_text(text)
    terms = [
        "版权所有",
        "网站管理",
        "技术支持",
        "友情链接",
        "官方微信",
        "部门简介",
        "办事平台",
        "简体中文",
        "更多联系方式",
        "处室电话",
        "联系我们",
    ]
    return sum(1 for term in terms if term in normalized)


def _remove_bad_elements(soup: BeautifulSoup) -> None:
    for selector in BAD_ELEMENT_SELECTORS:
        for node in soup.select(selector):
            node.decompose()


def _text_from_node(node: Any) -> str:
    for selector in BAD_ELEMENT_SELECTORS:
        for bad in node.select(selector):
            bad.decompose()
    return node.get_text("\n", strip=True)


def _score_candidate(text: str, base: int) -> int:
    lines = text.splitlines()
    long_lines = sum(1 for line in lines if len(line) >= 28)
    nav_score = boilerplate_score(text)
    return base + min(len(text), 5000) // 20 + long_lines * 8 - nav_score * 60


def _normalize_lines(text: str) -> list[str]:
    text = _normalize_text(text)
    lines = []
    for line in text.splitlines():
        stripped = re.sub(r"[ \t]{2,}", " ", line).strip()
        if stripped:
            lines.append(stripped)
    return lines


def _normalize_text(text: str) -> str:
    text = (text or "").replace("\xa0", " ")
    text = text.replace("\u3000", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _trim_before_title(lines: list[str], title: str) -> list[str]:
    normalized_title = _compact(title)
    if len(normalized_title) < 8:
        return lines
    for index, line in enumerate(lines):
        normalized_line = _compact(line)
        if normalized_title and normalized_title in normalized_line:
            before = lines[:index]
            if _looks_like_navigation_block(before):
                return lines[index:]
    return lines


def _drop_leading_title_and_date(lines: list[str], title: str) -> list[str]:
    output = list(lines)
    normalized_title = _compact(title)
    if normalized_title:
        while output and normalized_title in _compact(output[0]):
            output.pop(0)
    while output and DATE_LINE_RE.match(output[0]):
        output.pop(0)
    return output


def _truncate_footer(lines: list[str]) -> list[str]:
    for index, line in enumerate(lines):
        if any(pattern.search(line) for pattern in FOOTER_PATTERNS):
            return lines[:index]
    return lines


def _drop_noise_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    for index, line in enumerate(lines):
        compact = _compact(line)
        if not compact:
            continue
        if (line in NAV_LINE_EXACT or compact in NAV_LINE_COMPACT) and _looks_like_navigation_context(lines, index):
            continue
        if line in {"|", "/", "-", "—"}:
            continue
        if re.fullmatch(r"(?:简体中文\s*\|?\s*)?English", line):
            continue
        if len(line) <= 18 and line.endswith("..."):
            continue
        if len(line) <= 22 and re.match(r"东南大学20\d{2}\.\.\.$", line):
            continue
        if _nav_term_count(line) >= 4:
            continue
        output.append(line)
    return output


def _dedupe_nearby_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    seen_recent: list[str] = []
    for line in lines:
        compact = _compact(line)
        if _should_dedupe_line(compact) and compact in seen_recent:
            continue
        output.append(line)
        if _should_dedupe_line(compact):
            seen_recent.append(compact)
            if len(seen_recent) > 20:
                seen_recent.pop(0)
    return output


def _merge_fragment_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    current = ""
    for line in lines:
        if not current:
            current = line
            continue
        if _should_join_line(current, line):
            current = _join_inline(current, line)
            continue
        output.append(current)
        current = line
    if current:
        output.append(current)
    return output


def _drop_template_only_body(lines: list[str]) -> list[str]:
    text = "\n".join(lines).strip()
    compact = _compact(text)
    if len(compact) <= 220:
        has_nav_shell = compact.startswith("首页") or "友情链接" in compact
        has_footer_shell = any(term in compact for term in ("联系电话", "处室电话", "版权所有", "网站管理"))
        if has_nav_shell and has_footer_shell:
            return []
    return lines


def _looks_like_navigation_block(lines: list[str]) -> bool:
    if not lines:
        return False
    nav_lines = sum(1 for line in lines if _is_navigation_line(line))
    return nav_lines >= 6 or nav_lines >= max(3, len(lines) // 2)


def _is_navigation_line(line: str) -> bool:
    if line in NAV_LINE_EXACT:
        return True
    if re.fullmatch(r"(?:简体中文\s*\|?\s*)?English", line):
        return True
    if len(line) <= 24 and (_nav_term_count(line) >= 1 or line.endswith("...")):
        return True
    return _nav_term_count(line) >= 4


def _looks_like_navigation_context(lines: list[str], index: int) -> bool:
    start = max(0, index - 5)
    end = min(len(lines), index + 6)
    window = lines[start:end]
    nav_lines = sum(1 for line in window if _is_navigation_line(line))
    near_edge = index < 24 or index >= max(0, len(lines) - 24)
    return nav_lines >= 5 or (near_edge and nav_lines >= 3)


def _should_dedupe_line(compact: str) -> bool:
    return compact in NAV_LINE_COMPACT or _nav_term_count(compact) >= 2


def _should_join_line(current: str, line: str) -> bool:
    current_compact = _compact(current)
    line_compact = _compact(line)
    if not current_compact or not line_compact:
        return False
    if _looks_like_block_start(line):
        return False
    if len(current_compact) <= 12 and current.endswith(("：", ":")) and not line.startswith(("）", ")", "、")):
        return False
    if current.endswith(("（", "(", "、", "~", "～", "-", "—", "/", "->", "：", ":")):
        return True
    if line.startswith(("）", ")", "、", "，", "。", "；", "：", ":", "月", "日", "年", "级", "届", "分", "门", "项", "条")):
        return True
    if re.fullmatch(r"\d{1,4}(?:-\d{1,4})?", current_compact):
        return True
    if re.fullmatch(r"\d{1,2}[:：]\d{2}~?\d*", current_compact):
        return True
    if len(line_compact) <= 6 and not _ends_sentence(current):
        return True
    if len(current_compact) <= 18 and not _ends_sentence(current):
        return True
    return False


def _looks_like_block_start(line: str) -> bool:
    compact = _compact(line)
    if re.match(r"^[一二三四五六七八九十]+、", compact):
        return True
    if re.match(r"^附件\d*[:：]", compact):
        return True
    if re.match(r"^第\d+页[:：]", compact):
        return True
    return False


def _ends_sentence(text: str) -> bool:
    return text.endswith(("。", "！", "？", "；", ";"))


def _join_inline(left: str, right: str) -> str:
    if right.startswith(("，", "。", "；", "：", "、", "）", ")", "月", "日", "年", "级", "届", "分", "门", "项", "条")):
        return f"{left}{right}"
    if left.endswith(("（", "(", "、", "~", "～", "-", "—", "/", "->", "：", ":")):
        return f"{left}{right}"
    if re.search(r"[A-Za-z0-9]$", left) and re.match(r"^[A-Za-z0-9]", right):
        return f"{left} {right}"
    return f"{left}{right}"


def _nav_term_count(text: str) -> int:
    return sum(1 for term in NAV_TERMS if term in text)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")
