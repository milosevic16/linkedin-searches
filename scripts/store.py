"""Persist a run's results so the page can be rebuilt without paying again.

Searching LinkedIn is the expensive part of a build; rendering HTML is free.
Until now they were welded together — the pipeline's output lived only in
memory, so every rebuild re-ran every search, including rebuilds triggered by
editing a word in a config file.

This stores the gathered data in the repository, one file per company. A
rebuild then only needs to redo the stage whose inputs actually changed:

    keywords / search settings changed  ->  gather   (searches again)
    voice, profile or model changed     ->  redraft  (re-scores stored posts)
    anything else, or nothing           ->  render   (free)

Each company's data is separate, so a change to one can never trigger paid
work for the other.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GATHER, REDRAFT, RENDER = "gather", "redraft", "render"

# Everything ever shown, capped. Old entries fall off the front; a post that
# aged out and reappears simply reads as NEW again, which is harmless.
SEEN_CAP = 1500


def _digest(*parts) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def fingerprint(cfg: dict) -> dict:
    """Two hashes: what would invalidate the search, and what would invalidate
    the drafts. Anything not covered here only affects rendering.

    Both directions of error cost something. A key that is missing means an
    edit silently does nothing — posts_per_keyword could be raised from 10 to
    50 and the run would decide it had nothing to do. A key that is present
    but irrelevant to the active provider means an edit triggers a paid
    re-search that changes not one post, so the search digest only includes
    the keys the chosen provider actually reads.
    """
    search_cfg = cfg.get("search") or {}
    provider = (search_cfg.get("provider") or "apify").lower()

    common = [
        cfg.get("keywords"),
        provider,
        search_cfg.get("find_posts"),
        search_cfg.get("notable_max_age_hours"),
        search_cfg.get("notable_days"),
        search_cfg.get("include_articles"),
    ]
    if provider == "web":
        specific = [search_cfg.get("searches_per_keyword"), cfg.get("search_model")]
    else:
        specific = [
            search_cfg.get("posts_per_keyword"),
            search_cfg.get("sort_by"),
            search_cfg.get("max_usd_per_run"),
        ]

    return {
        "search": _digest(*common, *specific),
        "draft": _digest(
            cfg.get("profile"),
            cfg.get("voice"),
            cfg.get("commenters"),
            cfg.get("model"),
            cfg.get("score_model"),
            cfg.get("max_enriched"),
            cfg.get("min_relevance"),
        ),
    }


def save(company, *, topics: list[dict], posts: list[dict], warnings: list[str],
         usage: dict | None, generated_at: str, config_applied: bool = True) -> None:
    """Persist a run's result.

    config_applied=False records that this run did NOT successfully produce
    posts for the current settings — a search that failed, say. The fingerprint
    is left empty so the next --auto tries again. Stamping it as current would
    tell every later run that there was nothing to do, which is how a single
    failed search could bury a company's posts for good.
    """
    path = company.data_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "fingerprint": fingerprint(company.cfg) if config_applied else {},
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


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load(company) -> dict | None:
    path = company.data_path
    data = _read_json(path)
    if isinstance(data, dict):
        return data
    if path.exists():
        # Unreadable, but it is the only copy of work that was paid for. Move
        # it aside rather than letting the next save write straight over it —
        # returning None here means the caller sees "never gathered", and
        # without this that mistake would be permanent.
        salvage = path.with_suffix(".json.corrupt")
        try:
            path.replace(salvage)
            print(f"::error::{path.name} could not be read. Kept a copy at {salvage.name}.")
        except OSError:
            print(f"::error::{path.name} could not be read, and could not be moved aside.")
    return None


def load_seen(company) -> list[str] | None:
    """URLs already shown, or None if the file exists but could not be read.

    The distinction decides whether NEW badges are trustworthy: the list is
    written back every render, so treating an unreadable file as "nothing seen
    yet" would erase the history and re-flag every post as new. A file that is
    simply absent is different — that is a genuine first run.
    """
    if not company.seen_path.exists():
        return []
    data = _read_json(company.seen_path)
    if not isinstance(data, dict):
        return None
    urls = data.get("seen")
    if not isinstance(urls, list):
        return None
    return [u for u in urls if isinstance(u, str)]


def save_seen(company, urls: list[str]) -> None:
    path = company.seen_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"seen": urls[-SEEN_CAP:]}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


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
