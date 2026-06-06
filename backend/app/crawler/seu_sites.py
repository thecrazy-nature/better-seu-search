from __future__ import annotations

import ast
import hashlib
import json
import re
import time
from collections import deque
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..attachments import attachment_extension, extract_attachment_payload
from ..config import settings
from ..content_cleaning import extract_main_text
from ..date_utils import parse_date, publish_date_from_url
from ..models import SourceDocument
from ..preprocess import extract_keywords


USER_AGENT = "SEU-Search-MVP/0.1 (+student project; public pages only)"
GRADE_RE = re.compile(r"20\d{2}\s*级|大[一二三四五六]|研[一二三]|博士[一二三四五]")
STUDENT_TYPES = ["本科生", "研究生", "硕士", "博士", "留学生", "交换生"]
TOPIC_KEYWORDS = {
    "四六级": ["四六级", "四级", "六级", "CET", "大学英语四", "大学英语六"],
    "转专业": ["转专业", "专业调整"],
    "校历": ["校历", "教学日历", "放假", "上课时间"],
    "缓考": ["缓考", "考试延期"],
    "补考": ["补考", "重修考试"],
    "评教": ["评教", "教学评价"],
    "辅修": ["辅修", "微专业", "第二专业"],
    "选课": ["选课", "补退选"],
    "毕业": ["毕业", "学位", "离校"],
    "保研": ["推免", "推荐免试", "保研"],
}
FILE_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".jpg", ".jpeg", ".png")


SEED_SITES = [
    {
        "source": "教务处",
        "base": "https://jwc.seu.edu.cn/",
        "seeds": [
            "https://jwc.seu.edu.cn/",
            "https://jwc.seu.edu.cn/jwxx/list.htm",
            "https://jwc.seu.edu.cn/glgd/list.htm",
            "https://jwc.seu.edu.cn/bszn/list.htm",
            "https://jwc.seu.edu.cn/xzzq/list.htm",
            "https://jwc.seu.edu.cn/xl/list.htm",
        ],
    },
    {
        "source": "学校官网",
        "base": "https://www.seu.edu.cn/",
        "seeds": [
            "https://www.seu.edu.cn/",
        ],
    },
]


def configured_seed_sites() -> list[dict]:
    sites = list(SEED_SITES)
    if not settings.extra_seed_sites_json:
        return sites
    try:
        extra = json.loads(settings.extra_seed_sites_json)
    except json.JSONDecodeError:
        return sites
    if not isinstance(extra, list):
        return sites
    for item in extra:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        base = item.get("base")
        seeds = item.get("seeds")
        if isinstance(source, str) and isinstance(base, str) and isinstance(seeds, list):
            clean_seeds = [seed for seed in seeds if isinstance(seed, str)]
            if clean_seeds:
                sites.append(
                    {
                        "source": source,
                        "base": base,
                        "seeds": clean_seeds,
                        "include_path_prefixes": [],
                        "exclude_path_prefixes": [],
                        "max_depth": None,
                        "max_pages": None,
                        "days_back": None,
                    }
                )
    return sites


def normalized_site_config(site: dict) -> dict:
    source = str(site.get("source") or "").strip()
    base = str(site.get("base") or "").strip()
    seeds = [str(seed).strip() for seed in site.get("seeds") or [] if str(seed).strip()]
    include_path_prefixes = [
        str(prefix).strip() for prefix in site.get("include_path_prefixes") or [] if str(prefix).strip()
    ]
    exclude_path_prefixes = [
        str(prefix).strip() for prefix in site.get("exclude_path_prefixes") or [] if str(prefix).strip()
    ]
    return {
        "source": source,
        "base": base,
        "seeds": seeds or [base],
        "include_path_prefixes": include_path_prefixes,
        "exclude_path_prefixes": exclude_path_prefixes,
        "max_depth": site.get("max_depth"),
        "max_pages": site.get("max_pages"),
        "days_back": site.get("days_back"),
    }


def normalize_url(base_url: str, href: str) -> str | None:
    if not href or href.startswith(("javascript:", "mailto:", "#")):
        return None
    absolute = urljoin(base_url, href)
    absolute, _ = urldefrag(absolute)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc in {"jwc.seu.edu.cn", "www.seu.edu.cn"} and parsed.scheme == "http":
        absolute = "https://" + parsed.netloc + parsed.path
        if parsed.query:
            absolute += "?" + parsed.query
    return absolute


def hash_content(title: str, body: str, url: str, attachments: list[dict] | None = None) -> str:
    attachment_key = json.dumps(attachments or [], ensure_ascii=False, sort_keys=True)
    raw = f"{url}\n{title}\n{body}\n{attachment_key}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()


class PublicSiteCrawler:
    def __init__(
        self,
        max_pages_per_site: int | None = None,
        delay_seconds: float | None = None,
        sites: list[dict] | None = None,
    ) -> None:
        self.max_pages_per_site = max_pages_per_site or settings.crawl_max_pages_per_site
        self.delay_seconds = settings.crawl_delay_seconds if delay_seconds is None else delay_seconds
        self.max_depth = settings.crawl_max_depth
        self.cutoff_date = date.today() - timedelta(days=settings.crawl_days_back)
        self.sites = [normalized_site_config(site) for site in (sites or configured_seed_sites())]
        self.report: dict = {
            "cutoff_date": self.cutoff_date.isoformat(),
            "sites": {},
            "total_documents": 0,
        }
        self.client = httpx.Client(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def crawl_all(
        self,
        on_document: Callable[[SourceDocument], None] | None = None,
        verbose: bool = False,
    ) -> list[SourceDocument]:
        docs: list[SourceDocument] = []
        for site in self.sites:
            docs.extend(
                self.crawl_site(
                    site["source"],
                    site["base"],
                    site["seeds"],
                    include_path_prefixes=site.get("include_path_prefixes"),
                    exclude_path_prefixes=site.get("exclude_path_prefixes"),
                    max_depth=site.get("max_depth"),
                    max_pages=site.get("max_pages"),
                    cutoff_date=(
                        date.today() - timedelta(days=int(site["days_back"]))
                        if site.get("days_back")
                        else self.cutoff_date
                    ),
                    on_document=on_document,
                    verbose=verbose,
                )
            )
        self.report["total_documents"] = sum(item["document_count"] for item in self.report["sites"].values())
        return docs

    def crawl_site(
        self,
        source: str,
        base_url: str,
        seeds: list[str],
        include_path_prefixes: list[str] | None = None,
        exclude_path_prefixes: list[str] | None = None,
        max_depth: int | None = None,
        max_pages: int | None = None,
        cutoff_date: date | None = None,
        on_document: Callable[[SourceDocument], None] | None = None,
        verbose: bool = False,
    ) -> list[SourceDocument]:
        allowed_netloc = urlparse(base_url).netloc
        queue: deque[tuple[str, int]] = deque((seed, 0) for seed in seeds)
        seen: set[str] = set()
        docs: list[SourceDocument] = []
        site_max_depth = self.max_depth if max_depth is None else max_depth
        site_max_pages = self.max_pages_per_site if max_pages is None else max_pages
        site_cutoff_date = cutoff_date or self.cutoff_date
        include_path_prefixes = [prefix for prefix in include_path_prefixes or [] if prefix]
        exclude_path_prefixes = [prefix for prefix in exclude_path_prefixes or [] if prefix]
        site_report = {
            "source": source,
            "base_url": base_url,
            "seed_count": len(seeds),
            "include_path_prefixes": include_path_prefixes,
            "exclude_path_prefixes": exclude_path_prefixes,
            "max_depth": site_max_depth,
            "max_pages": site_max_pages,
            "cutoff_date": site_cutoff_date.isoformat(),
            "visited_count": 0,
            "document_count": 0,
            "skipped_old_count": 0,
            "file_link_count": 0,
        "list_pages": [],
        "article_pages": [],
        "skipped_document_pages": [],
        "errors": [],
        "hit_page_limit": False,
        }
        self.report["sites"][source] = site_report

        while queue and len(seen) < site_max_pages:
            url, depth = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            site_report["visited_count"] = len(seen)
            if self._is_file_url(url):
                site_report["file_link_count"] += 1
                continue
            response = self._get(url)
            if response is None:
                self._append_error(site_report, url, "request_failed")
                continue
            if "text/html" not in response.headers.get("content-type", ""):
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            if self._looks_like_list_page(url):
                site_report["list_pages"].append(url)
            if depth < site_max_depth:
                for link in self._extract_links(
                    soup,
                    url,
                    allowed_netloc,
                    include_path_prefixes=include_path_prefixes,
                    exclude_path_prefixes=exclude_path_prefixes,
                ):
                    if link not in seen and len(seen) + len(queue) < site_max_pages * 3:
                        queue.append((link, depth + 1))
            skip_reason = self._skip_document_reason(url, soup)
            if skip_reason:
                self._append_skipped_document(site_report, url, skip_reason)
                continue
            doc = self._parse_document(soup, url, source)
            if doc:
                if doc.publish_date and doc.publish_date < site_cutoff_date:
                    site_report["skipped_old_count"] += 1
                else:
                    if on_document:
                        on_document(doc)
                    else:
                        docs.append(doc)
                    site_report["document_count"] += 1
                    site_report["article_pages"].append(url)
            if verbose and len(seen) % 25 == 0:
                print(
                    f"[{source}] visited={len(seen)} docs={site_report['document_count']} "
                    f"old={site_report['skipped_old_count']} queue={len(queue)}",
                    flush=True,
                )
                self.write_report()
            time.sleep(self.delay_seconds)
        site_report["hit_page_limit"] = bool(queue and len(seen) >= site_max_pages)
        if verbose:
            print(
                f"[{source}] done visited={len(seen)} docs={site_report['document_count']} "
                f"old={site_report['skipped_old_count']} hit_limit={site_report['hit_page_limit']}",
                flush=True,
            )
            self.write_report()
        return docs

    def write_report(self) -> None:
        settings.crawl_report_path.parent.mkdir(parents=True, exist_ok=True)
        settings.crawl_report_path.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _append_error(site_report: dict, url: str, reason: str) -> None:
        if len(site_report["errors"]) < 80:
            site_report["errors"].append({"url": url, "reason": reason})

    @staticmethod
    def _append_skipped_document(site_report: dict, url: str, reason: str) -> None:
        if len(site_report["skipped_document_pages"]) < 2000:
            site_report["skipped_document_pages"].append({"url": url, "reason": reason})

    def _get(self, url: str) -> httpx.Response | None:
        try:
            response = self.client.get(url)
            response.raise_for_status()
            return response
        except Exception:
            return None

    def _extract_links(
        self,
        soup: BeautifulSoup,
        page_url: str,
        allowed_netloc: str,
        *,
        include_path_prefixes: list[str] | None = None,
        exclude_path_prefixes: list[str] | None = None,
    ) -> list[str]:
        links: list[str] = []
        for anchor in soup.find_all("a", href=True):
            url = normalize_url(page_url, anchor.get("href", ""))
            if not url:
                continue
            parsed = urlparse(url)
            if parsed.netloc != allowed_netloc:
                continue
            if self._is_file_url(url):
                continue
            if any(part in url for part in ["/_upload/", "/_t", "/main.htm"]):
                continue
            if not self._path_allowed(parsed.path, include_path_prefixes, exclude_path_prefixes):
                continue
            links.append(url)
        return links

    @staticmethod
    def _path_allowed(
        path: str,
        include_path_prefixes: list[str] | None = None,
        exclude_path_prefixes: list[str] | None = None,
    ) -> bool:
        clean_path = path or "/"
        excludes = [prefix for prefix in exclude_path_prefixes or [] if prefix]
        if any(clean_path.startswith(prefix) for prefix in excludes):
            return False
        includes = [prefix for prefix in include_path_prefixes or [] if prefix]
        if not includes:
            return True
        return any(clean_path.startswith(prefix) for prefix in includes)

    def _parse_document(self, soup: BeautifulSoup, url: str, source: str) -> SourceDocument | None:
        title = self._extract_title(soup)
        body = self._extract_body(soup, title)
        if not title:
            return None
        publish_date = self._extract_publish_date(soup, url)

        attachments = self._extract_attachments(soup, url)
        self._enrich_attachment_texts(attachments)
        if len(body) < 30 and not attachments:
            return None
        attachment_text = "\n".join(item.get("text", "") for item in attachments if item.get("text"))
        text_for_tags = f"{title}\n{body}\n{attachment_text}"
        return SourceDocument(
            title=title,
            url=url,
            source=source,
            category=self._guess_category(url, soup),
            publish_date=publish_date,
            body=body,
            attachments=attachments,
            applicable_colleges=self._extract_colleges(text_for_tags),
            applicable_grades=self._extract_grades(text_for_tags),
            student_types=self._extract_student_types(text_for_tags),
            topics=self._extract_topics(text_for_tags),
            keywords=extract_keywords(title, f"{body}\n{attachment_text}", attachments),
            deadline=self._extract_deadline(text_for_tags),
            content_hash=hash_content(title, body, url, attachments),
        )

    def _extract_title(self, soup: BeautifulSoup) -> str:
        candidates = []
        for selector in ["h1", ".arti_title", ".article-title", ".news_title", "title"]:
            node = soup.select_one(selector)
            if node:
                candidates.append(node.get_text(" ", strip=True))
        if not candidates:
            meta = soup.find("meta", property="og:title")
            if meta and meta.get("content"):
                candidates.append(meta["content"].strip())
        title = max(candidates, key=len, default="")
        title = re.sub(r"\s*-\s*东南大学.*$", "", title)
        title = re.sub(r"\s+", " ", title).strip()
        return title[:200]

    def _extract_body(self, soup: BeautifulSoup, title: str = "") -> str:
        return extract_main_text(soup, title=title, max_chars=30000)

    def _extract_publish_date(self, soup: BeautifulSoup, url: str) -> date | None:
        url_date = publish_date_from_url(url)
        if url_date:
            return url_date
        date_selectors = [
            ".arti_update",
            ".article-time",
            ".news-time",
            ".time",
            ".date",
            ".pubdate",
            ".publish",
        ]
        for selector in date_selectors:
            node = soup.select_one(selector)
            if node:
                value = parse_date(node.get_text(" ", strip=True))
                if value:
                    return value
        last_modified = soup.find("meta", attrs={"http-equiv": "last-modified"})
        if last_modified and last_modified.get("content"):
            try:
                return parsedate_to_datetime(last_modified["content"]).date()
            except Exception:
                return None
        return None

    def _skip_document_reason(self, url: str, soup: BeautifulSoup) -> str | None:
        if self._looks_like_home_or_index_page(url):
            return "home_or_index_page"
        if self._looks_like_list_page(url):
            return "list_page"
        if not self._has_article_container(soup):
            body_text = soup.get_text("\n", strip=True)
            if self._looks_like_listing_text(body_text):
                return "listing_like_page"
            return "no_article_container"
        return None

    def _extract_attachments(self, soup: BeautifulSoup, page_url: str) -> list[dict[str, str]]:
        attachments: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "")
            url = normalize_url(page_url, href)
            if not url or not self._is_file_url(url) or url in seen_urls:
                continue
            name = anchor.get_text(" ", strip=True) or url.rsplit("/", 1)[-1]
            attachments.append({"name": name[:160], "url": url})
            seen_urls.add(url)

        for node in soup.select("[pdfsrc], [filesrc], .wp_pdf_player, [sudyfile-attr]"):
            file_src = node.get("pdfsrc") or node.get("filesrc") or node.get("src") or ""
            url = normalize_url(page_url, str(file_src))
            if not url or not self._is_file_url(url) or url in seen_urls:
                continue
            name = self._embedded_attachment_name(node) or url.rsplit("/", 1)[-1]
            attachments.append({"name": name[:160], "url": url})
            seen_urls.add(url)
        return attachments

    @staticmethod
    def _embedded_attachment_name(node: object) -> str:
        get = getattr(node, "get", None)
        if not callable(get):
            return ""
        for key in ("title", "data-title", "filename", "filetitle"):
            value = get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raw_attr = get("sudyfile-attr")
        if isinstance(raw_attr, str) and raw_attr.strip():
            try:
                data = ast.literal_eval(raw_attr)
            except (SyntaxError, ValueError):
                data = {}
            if isinstance(data, dict):
                title = data.get("title") or data.get("name") or data.get("fileName")
                if isinstance(title, str) and title.strip():
                    return title.strip()
        text = getattr(node, "get_text", lambda *_args, **_kwargs: "")(" ", strip=True)
        return text.strip() if isinstance(text, str) else ""

    def _enrich_attachment_texts(self, attachments: list[dict[str, str]]) -> None:
        for item in attachments[:6]:
            url = item.get("url", "")
            if not url:
                continue
            ext = attachment_extension(url, item.get("name", ""))
            if ext not in {"", ".pdf", ".doc", ".docx", ".xls", ".xlsx"}:
                continue
            try:
                response = self.client.get(url)
                response.raise_for_status()
                if len(response.content) > 8 * 1024 * 1024:
                    continue
                payload = extract_attachment_payload(
                    response.content,
                    url,
                    item.get("name", ""),
                    response.headers.get("content-type", ""),
                )
            except Exception:
                payload = {"text": "", "pages": [], "sheets": []}
            if payload.get("text"):
                item["text"] = payload["text"]
                if payload.get("pages"):
                    item["pages"] = payload["pages"]
                if payload.get("sheets"):
                    item["sheets"] = payload["sheets"]

    @staticmethod
    def _is_file_url(url: str) -> bool:
        return urlparse(url).path.lower().endswith(FILE_EXTENSIONS)

    @staticmethod
    def _looks_like_list_page(url: str) -> bool:
        path = urlparse(url).path.lower()
        return path.endswith("/list.htm") or "/list" in path or "list" in path

    @staticmethod
    def _looks_like_home_or_index_page(url: str) -> bool:
        path = urlparse(url).path.lower().strip("/")
        return path in {"", "main.htm", "index.htm", "index.html", "default.htm"}

    @staticmethod
    def _has_article_container(soup: BeautifulSoup) -> bool:
        selectors = [
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
        ]
        return any(soup.select_one(selector) for selector in selectors)

    @staticmethod
    def _looks_like_listing_text(text: str) -> bool:
        if not text:
            return False
        date_count = len(re.findall(r"20\d{2}[-./年]\d{1,2}[-./月]\d{1,2}", text))
        linkish_lines = [line for line in text.splitlines() if len(line.strip()) >= 8]
        return date_count >= 5 and len(linkish_lines) >= 12

    @staticmethod
    def _guess_category(url: str, soup: BeautifulSoup) -> str | None:
        path = urlparse(url).path
        mapping = {
            "jwxx": "教务信息",
            "glgd": "管理规定",
            "bszn": "办事指南",
            "xzzq": "下载专区",
            "xl": "校历",
            "tzgg": "通知公告",
            "news": "新闻",
        }
        for key, value in mapping.items():
            if key in path:
                return value
        breadcrumb = soup.select_one(".breadcrumb, .location, .position")
        if breadcrumb:
            text = breadcrumb.get_text(" ", strip=True)
            return text[-40:]
        return None

    @staticmethod
    def _extract_colleges(text: str) -> list[str]:
        colleges = set(re.findall(r"[\u4e00-\u9fff]{2,20}(?:学院|书院|系)", text))
        cleaned = []
        for college in colleges:
            if college in {"各学院", "相关学院", "所在学院", "学生所在学院"}:
                continue
            cleaned.append(college)
        return sorted(cleaned)[:12]

    @staticmethod
    def _extract_grades(text: str) -> list[str]:
        return sorted(set(match.replace(" ", "") for match in GRADE_RE.findall(text)))[:12]

    @staticmethod
    def _extract_student_types(text: str) -> list[str]:
        return [item for item in STUDENT_TYPES if item in text]

    @staticmethod
    def _extract_topics(text: str) -> list[str]:
        topics = []
        for topic, keywords in TOPIC_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                topics.append(topic)
        return topics

    @staticmethod
    def _extract_deadline(text: str) -> date | None:
        deadline_patterns = [
            r"(?:截止|截至|报名时间.*?至|申请时间.*?至|提交.*?至).{0,30}?(20\d{2}[-./年]\d{1,2}[-./月]\d{1,2})",
            r"(20\d{2}[-./年]\d{1,2}[-./月]\d{1,2}).{0,20}?(?:截止|前完成|前提交|前报名)",
        ]
        for pattern in deadline_patterns:
            match = re.search(pattern, text)
            if match:
                return parse_date(match.group(1))
        return None
