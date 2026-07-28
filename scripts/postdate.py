"""Work out when a LinkedIn post was published, and how old that makes it.

Shared by every source of posts, because getting this wrong is what put a
2023 post on the dashboard with a relevance of 7. The rule is: a post whose
age cannot be established is discarded, never assumed recent.

The date comes from the post's own URL. LinkedIn activity IDs are
snowflake-style — the top 41 bits are a millisecond timestamp — so the URL
is a more reliable source than any date field a scraper or search engine
chooses to include, and it cannot be absent on a real post URL.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

_ACTIVITY_RE = re.compile(r"activity[-:](\d{18,20})")
_SNOWFLAKE_SHIFT = 22

# Plausible range for a decoded timestamp: 2010-01-01 .. 2100-01-01 in ms.
# Outside it the ID was not a snowflake, and a wrong date is worse than none.
_MIN_MS, _MAX_MS = 1_262_304_000_000, 4_102_444_800_000

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%B %d, %Y",
    "%b %d, %Y",
    "%Y-%m-%d",
    "%d %B %Y",
)


def _from_activity_id(url: str) -> datetime | None:
    m = _ACTIVITY_RE.search(url or "")
    if not m:
        return None
    ms = int(m.group(1)) >> _SNOWFLAKE_SHIFT
    if _MIN_MS < ms < _MAX_MS:
        return datetime.fromtimestamp(ms / 1000, timezone.utc)
    return None


def _from_text(value) -> datetime | None:
    if value is None:
        return None
    # Epoch seconds or milliseconds, however the source chose to express it.
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        n = float(value)
        if n > 1e11:  # milliseconds
            n /= 1000
        if _MIN_MS / 1000 < n < _MAX_MS / 1000:
            return datetime.fromtimestamp(n, timezone.utc)
        return None
    text = str(value).strip().replace("+00:00", "Z")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def post_datetime(url: str, *fallbacks) -> datetime | None:
    """Publication time for a post, or None if it cannot be established.

    The URL is tried first; `fallbacks` are any date-ish values the source
    supplied, tried in order.
    """
    return _from_activity_id(url) or next(
        (dt for dt in (_from_text(f) for f in fallbacks) if dt is not None), None
    )


def age_hours(when: datetime) -> float:
    return (datetime.now(timezone.utc) - when).total_seconds() / 3600


def max_age_hours(search_cfg: dict) -> float:
    """The configured age limit, in hours. `notable_days` is still honoured
    so an older config keeps working."""
    if search_cfg.get("notable_max_age_hours") is not None:
        return float(search_cfg["notable_max_age_hours"])
    return float(search_cfg.get("notable_days", 1)) * 24


def describe_window(hours: float) -> str:
    return f"{hours / 24:.0f} days" if hours >= 48 else f"{hours:.0f} hours"
