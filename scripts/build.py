"""Build the dashboard.

Gathering posts costs money; rendering the page does not. These are separate
stages so that rebuilding the site does not re-run the searches.

Usage:
  python scripts/build.py --gather    # full run: search, score, draft, render
  python scripts/build.py --redraft   # re-score stored posts (voice changed)
  python scripts/build.py --render    # rebuild the page only — no API calls
  python scripts/build.py --auto      # pick the cheapest sufficient mode
  python scripts/build.py --sample    # offline preview with bundled sample data

With no flag it gathers, so a plain scheduled run behaves as it always has.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml

import store
from enrich import enrich_posts
from launcher import build_launcher
from render import render
from search_claude import search_posts
from usage import Usage

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    with open(ROOT / "config.yml", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _mode_from_argv(argv: list[str]) -> str:
    for flag, mode in (
        ("--gather", store.GATHER),
        ("--redraft", store.REDRAFT),
        ("--render", store.RENDER),
    ):
        if flag in argv:
            return mode
    return "auto" if "--auto" in argv else store.GATHER


def _run_sample(cfg: dict) -> int:
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


def main() -> int:
    cfg = load_config()

    if "--sample" in sys.argv:
        return _run_sample(cfg)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("::warning::ANTHROPIC_API_KEY not set — rendering setup page.")
        render([], cfg, warnings=[], configured=False, topics=build_launcher(cfg)[0])
        return 0

    stored = store.load()
    mode = _mode_from_argv(sys.argv)
    no_gather = "--no-gather" in sys.argv
    warnings: list[str] = []

    if mode == "auto":
        mode, reason = store.decide_mode(cfg, stored)
        print(f"Auto mode: {mode} — {reason}.")
    elif mode in (store.REDRAFT, store.RENDER) and stored is None and not no_gather:
        print(f"::warning::No stored data to {mode} from — gathering instead.")
        mode = store.GATHER

    # Searching is the only step that costs real money, so it never happens
    # as a side effect of editing config.yml — only when someone asks for it.
    if no_gather and mode == store.GATHER:
        if stored is None:
            print("::warning::No stored data yet, and gathering was not requested.")
            warnings.append(
                "No posts have been gathered yet. Use the Refresh button above to run "
                "the first search."
            )
        else:
            print("Would gather, but --no-gather is set — rendering stored data instead.")
            warnings.append(
                "Your keywords or search settings changed, but searching costs money so it "
                "does not happen automatically. The posts below are from the previous "
                "search — use the Refresh button above to search with the new settings."
            )
        mode = store.RENDER

    usage = Usage()
    now = datetime.now(timezone.utc).isoformat()

    if mode == store.GATHER:
        # 1. Fresh half: deep links into LinkedIn's own search + reusable angles.
        topics, w = build_launcher(cfg, usage=usage)
        warnings += w
        print(f"Prepared {len(topics)} live search links.")

        # 2. Searching for individual posts is optional, and off by default:
        #    web search sees LinkedIn months late, so it cannot supply posts
        #    recent enough to be worth commenting on. See find_posts in
        #    config.yml.
        if (cfg.get("search") or {}).get("find_posts", False):
            posts, w = search_posts(cfg, usage=usage)
            warnings += w
            print(f"Found {len(posts)} posts within the age limit.")

            posts, w = enrich_posts(posts, cfg, usage=usage)
            warnings += w
        else:
            posts = []
            print("Post search is off (search.find_posts) — links and angles only.")
        gathered_at = now

    elif mode == store.REDRAFT:
        posts = stored.get("posts") or []
        gathered_at = stored.get("generated_at") or now
        print(f"Re-scoring {len(posts)} stored posts — no searching.")
        for post in posts:  # drop the old judgement so it is genuinely redone
            post.pop("relevance", None)
            post.pop("reason", None)
            post.pop("comments", None)
        topics, w = build_launcher(cfg, usage=usage)  # angles follow the voice too
        warnings += w
        posts, w = enrich_posts(posts, cfg, usage=usage)
        warnings += w

    else:  # RENDER — stored may be absent if nothing has been gathered yet
        posts = (stored or {}).get("posts") or []
        topics = (stored or {}).get("topics") or []
        warnings += (stored or {}).get("warnings") or []
        gathered_at = (stored or {}).get("generated_at") or now
        if not topics:  # nothing gathered yet — the links still work, and are free
            topics, w = build_launcher(cfg, with_angles=False)
            warnings += w
        print(f"Rendering {len(posts)} stored posts — no API calls.")

    # With post search off, previously-gathered posts must not keep rendering:
    # they are exactly the stale material that turning it off was meant to stop
    # showing. This applies in every mode, including a plain re-render.
    if not (cfg.get("search") or {}).get("find_posts", False) and posts:
        print(f"Post search is off — hiding {len(posts)} previously gathered posts.")
        posts = []

    # Render first so the is_new flags it sets get persisted, but save in a
    # finally: a rendering bug must not throw away searches we already paid
    # for. Re-running with --render then rebuilds the page for free.
    try:
        render(
            posts,
            cfg,
            warnings=warnings,
            configured=True,
            topics=topics,
            usage=usage.summary() if mode != store.RENDER else (stored.get("usage") or {}),
            gathered_at=gathered_at,
            mark_new=(mode == store.GATHER),
        )
    finally:
        if mode != store.RENDER:
            store.save(
                cfg=cfg,
                topics=topics,
                posts=posts,
                warnings=warnings,
                usage=usage.summary(),
                generated_at=gathered_at,
            )
            print(f"Stored run data in {store.DATA_PATH.relative_to(ROOT)}")

    print()
    print(usage.report())
    print()
    for w in warnings:
        print(f"::warning::{w}")
    print(f"Dashboard written to {ROOT / 'site' / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
