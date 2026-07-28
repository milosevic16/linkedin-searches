"""Persist a run's results so the page can be rebuilt without paying again.

Searching LinkedIn is the expensive part of a build; rendering HTML is free.
Until now they were welded together — the pipeline's output lived only in
memory, so every rebuild re-ran every search, including rebuilds triggered by
editing a word in config.yml.

This stores the gathered data in the repository. A rebuild then only needs to
redo the stage whose inputs actually changed:

    keywords / search settings changed  ->  gather   (searches again)
    voice or profile changed            ->  redraft  (re-scores stored posts)
    anything else, or nothing           ->  render   (free)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "latest.json"

GATHER, REDRAFT, RENDER = "gather", "redraft", "render"


def _digest(*parts) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def fingerprint(cfg: dict) -> dict:
    """Two hashes: what would invalidate the search, and what would invalidate
    the drafts. Anything not covered here only affects rendering."""
    search_cfg = cfg.get("search") or {}
    return {
        "search": _digest(
            cfg.get("keywords"),
            search_cfg.get("notable_days"),
            search_cfg.get("include_articles"),
            search_cfg.get("searches_per_keyword"),
            cfg.get("search_model"),
        ),
        "draft": _digest(
            cfg.get("profile"),
            cfg.get("voice"),
            cfg.get("model"),
            cfg.get("max_enriched"),
        ),
    }


def save(*, cfg: dict, topics: list[dict], posts: list[dict], warnings: list[str],
         usage: dict | None, generated_at: str) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "fingerprint": fingerprint(cfg),
                "topics": topics,
                "posts": posts,
                "warnings": warnings,
                "usage": usage or {},
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


def load() -> dict | None:
    if not DATA_PATH.exists():
        return None
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def decide_mode(cfg: dict, stored: dict | None) -> tuple[str, str]:
    """Pick the cheapest mode that still reflects the current config.
    Returns (mode, human-readable reason)."""
    if stored is None:
        return GATHER, "no stored data yet"

    current = fingerprint(cfg)
    previous = stored.get("fingerprint") or {}

    if current["search"] != previous.get("search"):
        return GATHER, "keywords or search settings changed"
    if current["draft"] != previous.get("draft"):
        return REDRAFT, "voice, profile or model changed — re-scoring stored posts"
    return RENDER, "config unchanged — rebuilding the page from stored data"
