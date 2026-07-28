"""Build the dashboard end-to-end: search → enrich → render.

Usage:
  python scripts/build.py            # real run (needs ANTHROPIC_API_KEY)
  python scripts/build.py --sample   # offline preview with bundled sample data
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml

from enrich import enrich_posts
from launcher import build_launcher
from render import render
from search_claude import search_posts

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    with open(ROOT / "config.yml", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def main() -> int:
    cfg = load_config()

    if "--sample" in sys.argv:
        posts = json.loads((ROOT / "scripts" / "sample_posts.json").read_text(encoding="utf-8"))
        topics, _ = build_launcher(cfg)  # links need no API key
        render(
            posts,
            cfg,
            warnings=["Sample data — this is an offline preview. The LinkedIn links above are real."],
            topics=topics,
        )
        print(f"Sample dashboard written to {ROOT / 'site' / 'index.html'}")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("::warning::ANTHROPIC_API_KEY not set — rendering setup page.")
        render([], cfg, warnings=[], configured=False, topics=build_launcher(cfg)[0])
        return 0

    # 1. Fresh half: deep links into LinkedIn's own search + reusable angles.
    topics, warnings = build_launcher(cfg)
    print(f"Prepared {len(topics)} live search links.")

    # 2. Notable half: what web search can actually see (weeks/months old).
    posts, search_warnings = search_posts(cfg)
    warnings += search_warnings
    print(f"Found {len(posts)} notable posts.")

    posts, enrich_warnings = enrich_posts(posts, cfg)
    warnings += enrich_warnings

    render(posts, cfg, warnings=warnings, configured=True, topics=topics)
    for w in warnings:
        print(f"::warning::{w}")
    print(f"Dashboard written to {ROOT / 'site' / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
