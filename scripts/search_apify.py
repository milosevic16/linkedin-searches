"""Fetch recent LinkedIn posts by keyword, via Apify.

Web search cannot do this job: search engines index LinkedIn months late, so
nothing they return is recent enough to be worth commenting on. This instead
calls a scraping actor that queries LinkedIn's own post search, which is where
recent posts actually exist.

Deliberately kept behind one small module. These vendors are not permanent —
Proxycurl, the biggest of them, was sued by LinkedIn and shut down in 2025 —
so swapping providers should mean rewriting this file and nothing else.

The order of operations matters and is not negotiable: fetch, then discard
anything outside the age limit, then match keywords, and only then send what
survives to a model. Every filter that can be done in code is done in code,
because a model asked to "skip old posts" cannot see a date and will not.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from postdate import age_hours, describe_window, max_age_hours, post_datetime

ACTOR = "harvestapi~linkedin-post-search"
ENDPOINT = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"

# The actor's published rate. Billed on posts returned, not posts kept, so
# the filters below save Claude tokens but not this — posts_per_keyword is
# the dial that moves it.
USD_PER_1000_POSTS = 2.00

# The actor's own timeout; a long tail of keywords can take a while.
_TIMEOUT_S = 540

# Candidate field names, tried in order. The actor's exact output shape is not
# something to guess at from documentation, so read whichever of these exists
# and report the truth on the first run (see _report_shape).
_URL_KEYS = ("url", "postUrl", "post_url", "link", "permalink")
_TEXT_KEYS = ("content", "text", "postContent", "post_text", "description", "commentary")
_DATE_KEYS = ("postedAt", "posted_at", "publishedAt", "published_at", "date",
              "postedDate", "createdAt", "time", "postedAtTimestamp")
_AUTHOR_KEYS = ("author", "authorName", "author_name", "actor", "profile", "user")
_NAME_KEYS = ("name", "fullName", "full_name", "title", "displayName", "username")


def _first(raw: dict, keys) -> object | None:
    for k in keys:
        v = raw.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _author_name(raw: dict) -> str:
    value = _first(raw, _AUTHOR_KEYS)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        name = _first(value, _NAME_KEYS)
        if isinstance(name, str):
            return name.strip()
        first = str(value.get("firstName") or "").strip()
        last = str(value.get("lastName") or "").strip()
        if first or last:
            return f"{first} {last}".strip()
    return ""


def _report_shape(items: list[dict], warnings: list[str]) -> None:
    """Say what the actor actually returned, once, so a changed output schema
    shows up as a message rather than as an empty dashboard."""
    if not items:
        return
    sample = items[0]
    if not isinstance(sample, dict):
        warnings.append(f"Apify returned {type(sample).__name__} items, not objects — cannot read them.")
        return
    print(f"Apify item fields: {', '.join(sorted(sample.keys()))}")
    if _first(sample, _URL_KEYS) is None:
        warnings.append(
            "Apify results carry no recognisable URL field "
            f"(saw: {', '.join(sorted(sample.keys()))}). The actor's output format has "
            "probably changed — scripts/search_apify.py needs its field names updated."
        )


def _call_actor(token: str, queries: list[str], max_posts: int, sort_by: str) -> list[dict]:
    payload = {
        "searchQueries": queries,
        "maxPosts": max_posts,
        "sortBy": sort_by,          # newest first, so the age filter keeps the most
        "scrapeReactions": False,   # not needed, and each extra fetch costs
        "scrapeComments": False,
    }
    req = urllib.request.Request(
        f"{ENDPOINT}?token={token}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        body = resp.read().decode("utf-8")
    items = json.loads(body)
    return items if isinstance(items, list) else []


def _matched_keywords(text: str, keywords: list[str]) -> list[str]:
    """Which topics a post belongs to. LinkedIn's search is fuzzy, so a result
    can come back for a query whose terms it does not contain — this is the
    keyword filter the search itself does not reliably apply."""
    haystack = text.lower()
    matched = []
    for kw in keywords:
        terms = [t for t in kw.lower().split() if len(t) > 2]
        if terms and sum(1 for t in terms if t in haystack) >= max(1, len(terms) - 1):
            matched.append(kw)
    return matched


def search_posts(cfg: dict, usage=None) -> tuple[list[dict], list[str]]:
    """Return (posts, warnings). Each post: url, title, author, snippet,
    keywords, posted_at, age_hours."""
    warnings: list[str] = []
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        return [], ["Post search is configured but APIFY_TOKEN is not set — no posts fetched."]

    keywords = [str(k).strip() for k in (cfg.get("keywords") or []) if str(k).strip()]
    if not keywords:
        return [], []

    search_cfg = cfg.get("search") or {}
    limit_hours = max_age_hours(search_cfg)
    per_keyword = int(search_cfg.get("posts_per_keyword", 20))
    max_posts = max(1, per_keyword * len(keywords))

    try:
        items = _call_actor(token, keywords, max_posts, search_cfg.get("sort_by", "date"))
    except urllib.error.HTTPError as exc:
        detail = "check the token is valid" if exc.code in (401, 403) else exc.reason
        return [], [f"Apify returned HTTP {exc.code} ({detail}) — no posts fetched this run."]
    except urllib.error.URLError as exc:
        return [], [f"Could not reach Apify ({exc.reason}) — no posts fetched this run."]
    except (TimeoutError, json.JSONDecodeError) as exc:
        return [], [f"Apify request failed ({type(exc).__name__}) — no posts fetched this run."]

    print(f"Apify returned {len(items)} posts for {len(keywords)} keywords.")
    if usage is not None:
        usage.record_external("apify", len(items), USD_PER_1000_POSTS)
    _report_shape(items, warnings)

    posts: dict[str, dict] = {}
    too_old = undatable = unmatched = 0

    for raw in items:
        if not isinstance(raw, dict):
            continue
        url = str(_first(raw, _URL_KEYS) or "").split("?")[0].rstrip("/")
        if not url:
            undatable += 1
            continue

        # 1. Date filter, in code, against the post's own URL.
        when = post_datetime(url, _first(raw, _DATE_KEYS))
        if when is None:
            undatable += 1
            continue
        age = age_hours(when)
        if age > limit_hours:
            too_old += 1
            continue

        # 2. Keyword filter, also in code.
        text = str(_first(raw, _TEXT_KEYS) or "").strip()
        matched = _matched_keywords(f"{text} {_author_name(raw)}", keywords)
        if not matched:
            unmatched += 1
            continue

        # 3. Only what survives both goes on to cost model tokens.
        existing = posts.get(url)
        if existing:
            for kw in matched:
                if kw not in existing["keywords"]:
                    existing["keywords"].append(kw)
            continue
        posts[url] = {
            "url": url,
            "title": text.split("\n", 1)[0][:180],
            "author": _author_name(raw) or "Unknown author",
            "snippet": text,
            "keywords": matched,
            "is_article": "/pulse/" in url,
            "posted_at": when.isoformat(),
            "age_hours": round(age, 1),
        }

    dropped = []
    if too_old:
        dropped.append(f"{too_old} older than {describe_window(limit_hours)}")
    if unmatched:
        dropped.append(f"{unmatched} not matching any keyword")
    if undatable:
        dropped.append(f"{undatable} with no verifiable date")
    if dropped:
        print(f"Filtered out: {', '.join(dropped)}.")
    if not posts and items:
        warnings.append(
            f"Apify returned {len(items)} posts but none survived filtering "
            f"({', '.join(dropped)}). Widen notable_max_age_hours if this keeps happening."
        )

    return list(posts.values()), warnings
