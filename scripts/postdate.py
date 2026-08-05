"""Work out when a LinkedIn post was published, and how old that makes it.

Shared by every source of posts, because getting this wrong is what put a
2023 post on the dashboard with a relevance of 7. The rule is: a post whose
age cannot be established is discarded, never assumed recent.

Dates come from the post's own URL where possible. LinkedIn activity IDs are
snowflake-style — the top 41 bits are a millisecond timestamp — so the URL is
more reliable than any date field a scraper chooses to include. Everything
else is a fallback, and each fallback exists because a real source used that
shape: bare ids, urn strings, epoch numbers, ISO strings, and objects with
the timestamp nested inside.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

# activity, ugcPost and share URNs all appear in LinkedIn post URLs, and all
# three carry the same snowflake id. Matching only "activity" silently threw
# away every share and every ugcPost.
_ACTIVITY_RE = re.compile(r"(?:activity|ugcPost|share)[-:](\d{18,20})")
_SNOWFLAKE_SHIFT = 22

# Plausible range for a decoded timestamp: 2010-01-01 .. 2100-01-01 in ms.
# Outside it the id was not a snowflake, and a wrong date is worse than none.
_MIN_MS, _MAX_MS = 1_262_304_000_000, 4_102_444_800_000

# Keys to look inside when a date arrives as an object rather than a scalar.
_NESTED_DATE_KEYS = ("timestamp", "date", "value", "time", "epoch", "iso")

_DATE_FORMATS = (
    "%B %d, %Y",
    "%b %d, %Y",
    "%Y-%m-%d",
    "%d %B %Y",
    "%d/%m/%Y",
)


def _from_activity_id(url: str) -> datetime | None:
    m = _ACTIVITY_RE.search(url or "")
    if not m:
        return None
    ms = int(m.group(1)) >> _SNOWFLAKE_SHIFT
    return datetime.fromtimestamp(ms / 1000, timezone.utc) if _MIN_MS < ms < _MAX_MS else None


def _from_bare_id(value) -> datetime | None:
    """A bare activity id, e.g. an entityId carrying 7392104744699187200 with
    no surrounding URL — or the same wrapped as urn:li:activity:…"""
    digits = str(value or "").strip().rsplit(":", 1)[-1]
    if not (digits.isdigit() and 18 <= len(digits) <= 20):
        return None
    ms = int(digits) >> _SNOWFLAKE_SHIFT
    return datetime.fromtimestamp(ms / 1000, timezone.utc) if _MIN_MS < ms < _MAX_MS else None


def _from_epoch(value) -> datetime | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n > 1e11:  # milliseconds rather than seconds
        n /= 1000
    return (
        datetime.fromtimestamp(n, timezone.utc)
        if _MIN_MS / 1000 < n < _MAX_MS / 1000
        else None
    )


def _from_text(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    # fromisoformat covers offsets like +02:00 that a fixed format table does
    # not. Python 3.11+ accepts a trailing Z; normalise anyway for older ones.
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse(value, depth: int = 0) -> datetime | None:
    """One candidate value, whatever shape it arrived in."""
    if value is None or depth > 3:
        return None
    # Sources return dates as objects surprisingly often — the Apify actor
    # sends {"timestamp": 178…, "date": "2026-…Z", "postedAgoShort": "5h"}.
    # Stringifying that yields a dict repr and no date at all.
    if isinstance(value, dict):
        for key in _NESTED_DATE_KEYS:
            if key in value and (dt := _parse(value[key], depth + 1)) is not None:
                return dt
        return None
    if isinstance(value, (list, tuple)):
        return next((dt for v in value if (dt := _parse(v, depth + 1))), None)
    if isinstance(value, bool):
        return None
    return _from_bare_id(value) or _from_epoch(value) or _from_text(value)


def post_candidates(url: str, *fallbacks) -> list[datetime]:
    """Every publication time the sources offer, oldest first."""
    found = []
    if (dt := _from_activity_id(url)) is not None:
        found.append(dt)
    for value in fallbacks:
        if (dt := _parse(value)) is not None:
            found.append(dt)
    return sorted(found)


def post_datetime(url: str, *fallbacks) -> datetime | None:
    """Publication time for a post, or None if it cannot be established.

    Where sources disagree, the OLDEST wins. That is not arbitrary. A reshare
    carries a brand-new activity id in its URL while the text is the original's,
    so trusting the URL put six-day-old content on the dashboard stamped "just
    now" — which is what it did. Preferring the oldest can only cost us a post
    we would otherwise have shown; the other direction presents stale posts as
    fresh, and a comment on a stale post is the one thing the age filter exists
    to prevent.
    """
    candidates = post_candidates(url, *fallbacks)
    return candidates[0] if candidates else None


def date_disagreement_hours(url: str, *fallbacks) -> float:
    """Spread between the oldest and newest candidate date, in hours. A large
    value means the sources are describing different events — usually a
    reshare, whose URL is new but whose content is not."""
    candidates = post_candidates(url, *fallbacks)
    return 0.0 if len(candidates) < 2 else (candidates[-1] - candidates[0]).total_seconds() / 3600


def age_hours(when: datetime) -> float:
    return (datetime.now(timezone.utc) - when).total_seconds() / 3600


def max_age_hours(search_cfg: dict) -> float:
    """The configured age limit, in hours. `notable_days` is still honoured
    so an older config keeps working."""
    if search_cfg.get("notable_max_age_hours") is not None:
        return float(search_cfg["notable_max_age_hours"])
    return float(search_cfg.get("notable_days", 2)) * 24


def describe_window(hours: float) -> str:
    return f"{hours / 24:.0f} days" if hours >= 48 else f"{hours:.0f} hours"


def source_side_limit(hours: float) -> str:
    """The nearest recency bucket LinkedIn's own search accepts.

    Only 24h / week / month are passed through to LinkedIn and filtered at
    source; the finer values the actor advertises are applied after results
    come back, so they cost the same as no filter at all. This is a coarse
    pre-filter to improve yield — the exact cut still happens in our code.
    """
    if hours <= 24:
        return "24h"
    if hours <= 168:
        return "week"
    return "month"
