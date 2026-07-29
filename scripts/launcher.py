"""Build the per-topic links into LinkedIn's own search.

Web search can't see posts from the last 48 hours (LinkedIn blocks crawlers),
so for freshness we hand the user straight into LinkedIn's OWN search, which
does see them — pre-filtered to recent and sorted newest-first, one click per
keyword.

This costs nothing: the links are just URLs. It used to also generate three
reusable comment angles per topic, from a time when we could not read any post
and so had nothing specific to write about. Posts now come with drafts written
for them individually, which is what the angles were standing in for.
"""

from __future__ import annotations

from urllib.parse import quote

SEARCH_BASE = "https://www.linkedin.com/search/results/content/"

# LinkedIn's own datePosted filter values.
WINDOWS = {
    "past-24h": "Past 24 hours",
    "past-week": "Past week",
    "past-month": "Past month",
}


def search_url(keyword: str, window: str = "past-24h", newest_first: bool = True) -> str:
    """A LinkedIn content-search URL, pre-filtered by recency."""
    params = [f"keywords={quote(keyword)}"]
    if window in WINDOWS:
        params.append(f'datePosted={quote(chr(34) + window + chr(34))}')
    if newest_first:
        params.append(f'sortBy={quote(chr(34) + "date_posted" + chr(34))}')
    return SEARCH_BASE + "?" + "&".join(params)


def build_launcher(cfg: dict) -> tuple[list[dict], list[str]]:
    """Return (topics, warnings). Each topic: keyword, window_label, fresh_url,
    week_url. No model is called, so this is free and cannot fail."""
    warnings: list[str] = []

    keywords, seen = [], set()
    for raw in cfg.get("keywords") or []:
        k = str(raw).strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            keywords.append(k)
    if not keywords:
        return [], warnings

    window = (cfg.get("search") or {}).get("fresh_window", "past-24h")
    if window not in WINDOWS:
        where = cfg.get("config_file") or "the company's config file"
        warnings.append(f"Unknown fresh_window “{window}” in {where} — using past-24h.")
        window = "past-24h"

    topics = [
        {
            "keyword": k,
            "window_label": WINDOWS[window],
            "fresh_url": search_url(k, window),
            "week_url": search_url(k, "past-week"),
        }
        for k in keywords
    ]
    return topics, warnings
