"""Build the 'fresh posts' half of the dashboard.

Web search can't see posts from the last 48 hours (LinkedIn blocks crawlers),
so for freshness we hand the user straight into LinkedIn's OWN search, which
does see them — pre-filtered to recent + sorted newest-first, one click per
keyword. We never see those posts, so instead of per-post drafts Claude
prepares reusable comment ANGLES per topic that work on whatever they find.
"""

from __future__ import annotations

import json
import os
from urllib.parse import quote

SEARCH_BASE = "https://www.linkedin.com/search/results/content/"

# LinkedIn's own datePosted filter values.
WINDOWS = {
    "past-24h": "Past 24 hours",
    "past-week": "Past week",
    "past-month": "Past month",
}

_ANGLES_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "angles": {
                        "type": "array",
                        "description": "3 reusable comment angles for this topic.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {
                                    "type": "string",
                                    "description": "3-5 word name for the angle, e.g. 'The cost nobody mentions'.",
                                },
                                "text": {
                                    "type": "string",
                                    "description": "1-2 sentences they can adapt to most posts on this topic.",
                                },
                            },
                            "required": ["label", "text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["keyword", "angles"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["topics"],
    "additionalProperties": False,
}

_SYSTEM = """You prepare LinkedIn commenting strategy for two professionals.

Who they are and why they comment:
{profile}

Comment voice:
{voice}

For each topic you are given, write 3 reusable comment ANGLES — perspectives \
they can adapt to almost any post on that topic. These are not replies to a \
specific post, so they must be general enough to fit many posts while still \
being concrete and opinionated.

A good angle contains a real position, a specific detail, or a sharp question \
that shows domain knowledge. Avoid generic praise, avoid anything that only \
works if the post said something particular, and avoid self-promotion."""


def search_url(keyword: str, window: str = "past-24h", newest_first: bool = True) -> str:
    """A LinkedIn content-search URL, pre-filtered by recency."""
    params = [f"keywords={quote(keyword)}"]
    if window in WINDOWS:
        params.append(f'datePosted={quote(chr(34) + window + chr(34))}')
    if newest_first:
        params.append(f'sortBy={quote(chr(34) + "date_posted" + chr(34))}')
    return SEARCH_BASE + "?" + "&".join(params)


def build_launcher(cfg: dict, usage=None, with_angles: bool = True) -> tuple[list[dict], list[str]]:
    """Return (topics, warnings). Each topic: keyword, fresh_url, week_url, angles."""
    warnings: list[str] = []
    keywords = [str(k).strip() for k in (cfg.get("keywords") or []) if str(k).strip()]
    if not keywords:
        return [], warnings

    window = (cfg.get("search") or {}).get("fresh_window", "past-24h")
    if window not in WINDOWS:
        warnings.append(f"Unknown fresh_window “{window}” in config.yml — using past-24h.")
        window = "past-24h"

    topics = [
        {
            "keyword": k,
            "window_label": WINDOWS[window],
            "fresh_url": search_url(k, window),
            "week_url": search_url(k, "past-week"),
            "angles": [],
        }
        for k in keywords
    ]

    # The links themselves are just URLs — free, and the useful half of this
    # section. Only the angles cost anything, so they can be skipped.
    if not with_angles or not os.environ.get("ANTHROPIC_API_KEY"):
        return topics, warnings

    import anthropic

    client = anthropic.Anthropic()
    # Writing three reusable talking points per topic is not a hard task, and
    # with post search off this is the only model call a refresh makes — so it
    # runs on the cheapest model rather than the best one. If the angles start
    # reading as generic, raise angles_model in config.yml; the whole call is
    # a few cents at any tier.
    angles_model = cfg.get("angles_model") or "claude-haiku-4-5"
    try:
        response = client.messages.create(
            model=angles_model,
            max_tokens=4000,
            system=_SYSTEM.format(
                profile=(cfg.get("profile") or "").strip(),
                voice=(cfg.get("voice") or "").strip(),
            ),
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _ANGLES_SCHEMA}},
            messages=[{"role": "user", "content": "Topics:\n" + json.dumps(keywords, ensure_ascii=False)}],
        )
    except Exception as exc:  # angles are a nice-to-have; links must still ship
        warnings.append(f"Could not prepare comment angles ({type(exc).__name__}) — links still work.")
        return topics, warnings

    if usage is not None:
        usage.record("angles", angles_model, response)

    if response.stop_reason == "refusal":
        warnings.append("Claude declined to prepare comment angles — links still work.")
        return topics, warnings

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        parsed = json.loads(text).get("topics", [])
    except (json.JSONDecodeError, AttributeError):
        warnings.append("Could not read the comment angles — links still work.")
        return topics, warnings

    by_keyword = {str(t.get("keyword", "")).strip(): t.get("angles") or [] for t in parsed}
    for topic in topics:
        for angle in by_keyword.get(topic["keyword"], [])[:3]:
            label = str(angle.get("label", "")).strip()
            body = str(angle.get("text", "")).strip()
            if body:
                topic["angles"].append({"label": label or "Angle", "text": body})

    return topics, warnings
