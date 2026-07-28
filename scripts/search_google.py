"""Find recent public LinkedIn posts via the Google Programmable Search API.

Requires two environment variables:
  GOOGLE_API_KEY  – Custom Search JSON API key (free tier: 100 queries/day)
  GOOGLE_CSE_ID   – Programmable Search Engine ID restricted to
                    linkedin.com/posts/* (and optionally linkedin.com/pulse/*)
"""

from __future__ import annotations

import os
import re

import requests

API_URL = "https://www.googleapis.com/customsearch/v1"

# Google usually titles indexed LinkedIn posts as "<Author> on LinkedIn: <text>"
_TITLE_RE = re.compile(r"^(.{2,80}?)\s+on LinkedIn:?\s*(.*)$", re.S)


def _parse_title(title: str) -> tuple[str, str]:
    """Split a Google result title into (author, headline). Best effort."""
    m = _TITLE_RE.match(title or "")
    if m:
        return m.group(1).strip(), (m.group(2) or "").strip()
    return "", (title or "").strip()


def _clean_snippet(snippet: str) -> str:
    # Snippets often lead with "3 days ago ... " — keep it, it's useful context,
    # but normalise whitespace.
    return re.sub(r"\s+", " ", snippet or "").strip()


def search_posts(cfg: dict) -> tuple[list[dict], list[str]]:
    """Run one Google query per keyword; return (posts, warnings).

    Each post: {url, title, author, snippet, keywords: [..]}
    Deduplicated by URL across keywords.
    """
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    cse_id = os.environ.get("GOOGLE_CSE_ID", "")
    warnings: list[str] = []
    if not api_key or not cse_id:
        return [], ["Google Search is not configured (GOOGLE_API_KEY / GOOGLE_CSE_ID missing)."]

    search_cfg = cfg.get("search") or {}
    days = int(search_cfg.get("days_back", 3))
    pages = max(1, min(3, int(search_cfg.get("pages_per_keyword", 2))))
    include_articles = bool(search_cfg.get("include_articles", True))

    posts: dict[str, dict] = {}
    for keyword in cfg.get("keywords") or []:
        keyword = str(keyword).strip()
        if not keyword:
            continue
        for page in range(pages):
            params = {
                "key": api_key,
                "cx": cse_id,
                "q": keyword,
                "dateRestrict": f"d{days}",
                "num": 10,
                "start": 1 + page * 10,
            }
            try:
                resp = requests.get(API_URL, params=params, timeout=30)
            except requests.RequestException as exc:
                warnings.append(f"Google search failed for “{keyword}”: {exc}")
                break
            if resp.status_code == 429:
                warnings.append(
                    "Google Search daily quota reached — results may be incomplete "
                    "(free tier is 100 queries/day; try fewer keywords or pages)."
                )
                return list(posts.values()), warnings
            if resp.status_code != 200:
                detail = ""
                try:
                    detail = resp.json().get("error", {}).get("message", "")
                except Exception:
                    pass
                warnings.append(f"Google search error {resp.status_code} for “{keyword}”: {detail}")
                break

            items = resp.json().get("items") or []
            for item in items:
                url = (item.get("link") or "").split("?")[0].rstrip("/")
                if "/posts/" not in url and not (include_articles and "/pulse/" in url):
                    continue
                author, headline = _parse_title(item.get("title") or "")
                post = posts.setdefault(
                    url,
                    {
                        "url": url,
                        "title": headline,
                        "author": author,
                        "snippet": _clean_snippet(item.get("snippet") or ""),
                        "keywords": [],
                        "is_article": "/pulse/" in url,
                    },
                )
                if keyword not in post["keywords"]:
                    post["keywords"].append(keyword)

            if len(items) < 10:
                break  # no further pages for this keyword

    return list(posts.values()), warnings
