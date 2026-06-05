from __future__ import annotations

import re
from collections import Counter

from .models import SourceDocument
from .search.synonyms import SYNONYMS


STOPWORDS = {
    "关于",
    "通知",
    "工作",
    "进行",
    "相关",
    "有关",
    "根据",
    "学校",
    "学生",
    "各位",
    "各院",
    "各学院",
    "东南大学",
}

DOMAIN_TERMS = [
    "四六级",
    "CET",
    "转专业",
    "专业调整",
    "校历",
    "缓考",
    "补考",
    "评教",
    "辅修",
    "选课",
    "成绩复核",
    "成绩单",
    "毕业审核",
    "推免",
    "保研",
    "奖学金",
    "培养方案",
    "考试安排",
    "申请表",
    "报名",
    "截止",
    "下载",
]

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}|20\d{2}级|20\d{2}|[\u4e00-\u9fff]{2,8}")


def extract_keywords(title: str, body: str, attachments: list[dict[str, str]] | None = None, limit: int = 24) -> list[str]:
    attachments = attachments or []
    text = "\n".join(
        [
            title or "",
            body[:12000] if body else "",
            " ".join(item.get("name", "") for item in attachments),
        ]
    )
    weighted: Counter[str] = Counter()
    for term in DOMAIN_TERMS:
        if term and term in text:
            weighted[term] += 8
    for topic, aliases in SYNONYMS.items():
        if topic in text or any(alias in text for alias in aliases):
            weighted[topic] += 10
            for alias in aliases:
                if alias in text:
                    weighted[alias] += 4
    for token in TOKEN_RE.findall(text):
        token = token.strip()
        if len(token) < 2 or token in STOPWORDS:
            continue
        if token.isdigit() and len(token) != 4:
            continue
        weighted[token] += 3 if token in title else 1
    return [item for item, _ in weighted.most_common(limit)]


def enrich_document(doc: SourceDocument) -> SourceDocument:
    keywords = doc.keywords or extract_keywords(doc.title, doc.body, doc.attachments)
    topics = list(doc.topics)
    for topic, aliases in SYNONYMS.items():
        if topic in keywords or any(alias in keywords for alias in aliases):
            if topic not in topics:
                topics.append(topic)
    doc.keywords = keywords
    doc.topics = topics
    return doc
