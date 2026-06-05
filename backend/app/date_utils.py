from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse


DATE_RE = re.compile(r"(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})")
URL_DATE_RE = re.compile(r"/(20\d{2})/(\d{2})(\d{2})/")


def parse_date(text: str) -> date | None:
    match = DATE_RE.search(text or "")
    if not match:
        return None
    year, month, day = map(int, match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def publish_date_from_url(url: str) -> date | None:
    match = URL_DATE_RE.search(urlparse(url).path)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None
