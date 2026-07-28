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
        render(posts, cfg, warnings=["Sample data — this is an offline preview, not real search results."])
        print(f"Sample dashboard written to {ROOT / 'site' / 'index.html'}")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("::warning::ANTHROPIC_API_KEY not set — rendering setup page.")
        render([], cfg, warnings=[], configured=False)
        return 0

    posts, warnings = search_posts(cfg)
    print(f"Found {len(posts)} candidate posts.")

    posts, enrich_warnings = enrich_posts(posts, cfg)
    warnings += enrich_warnings

    render(posts, cfg, warnings=warnings, configured=True)
    for w in warnings:
        print(f"::warning::{w}")
    print(f"Dashboard written to {ROOT / 'site' / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
