"""Build the dashboards.

Gathering posts costs money; rendering the page does not. These are separate
stages so that rebuilding the site does not re-run the searches.

Usage:
  python scripts/build.py --company bloctopus --gather   # search, score, draft
  python scripts/build.py --company bloctopus --redraft  # re-score stored posts
  python scripts/build.py --render                       # rebuild pages, free
  python scripts/build.py --auto --company bloctopus     # cheapest sufficient
  python scripts/build.py --sample                       # offline preview

Paid work is always scoped to ONE company, named with --company. Rendering is
not: every run rebuilds every company's page, because GitHub Pages replaces the
whole site on each deploy — publishing one company's page alone would 404 the
other's.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import companies as registry
import store
from enrich import enrich_posts
from search_apify import SearchFailed
from launcher import build_launcher
from render import render
from usage import Usage

ROOT = Path(__file__).resolve().parent.parent


def _search_provider(cfg: dict):
    """Where posts come from. Kept swappable because these vendors are not
    permanent — Proxycurl was sued by LinkedIn and shut down in 2025."""
    name = ((cfg.get("search") or {}).get("provider") or "apify").lower()
    if name == "web":
        from search_claude import search_posts  # Claude's web search tool
        return search_posts, "web search"
    from search_apify import search_posts       # LinkedIn's own search, via Apify
    return search_posts, "Apify"


def _mode_from_argv(argv: list[str]) -> str:
    for flag, mode in (
        ("--gather", store.GATHER),
        ("--redraft", store.REDRAFT),
        ("--render", store.RENDER),
    ):
        if flag in argv:
            return mode
    return "auto" if "--auto" in argv else store.GATHER


def _company_from_argv(argv: list[str]) -> str | None:
    if "--company" in argv:
        i = argv.index("--company")
        if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            return argv[i + 1]
        raise registry.ConfigError("--company needs a name, e.g. --company bloctopus.")
    return None


def _run_sample(companies: list, endpoint: str) -> int:
    posts = json.loads((ROOT / "scripts" / "sample_posts.json").read_text(encoding="utf-8"))
    for company in companies:
        topics, _ = build_launcher(company.cfg)  # links need no API key
        # mark_new=False: the sample URLs are fabricated, and letting them into
        # the seen-history would mean real posts with those URLs never showed
        # as new again.
        # persist_seen=False: these URLs are fabricated. Letting them into the
        # history would mean a real post that later used one never showed as new.
        render(
            posts if company.is_default else [],
            company,
            warnings=["Sample data — this is an offline preview. The LinkedIn links above are real."],
            topics=topics,
            endpoint=endpoint,
            siblings=companies,
            mark_new=False,
            persist_seen=False,
        )
    print(f"Sample dashboard written to {companies[0].site_dir / 'index.html'}")
    return 0


def _note_stale(company, stored: dict | None) -> None:
    """Log that a company's settings have outrun its stored posts.

    Deliberately the build log only, not the page. It is true from the moment a
    config is edited until someone pays for a Refresh, so on the page it was a
    permanent fixture rather than news — and the header already says how old the
    posts are, which is the part a reader actually acts on.
    """
    if stored is None:
        return
    mode, reason = store.decide_mode(company.cfg, stored)
    if mode != store.RENDER:
        print(f"{company.slug}: {reason} — press Refresh to act on it ({mode} is not free).")


def _render_stored(company, endpoint: str, companies: list) -> None:
    """Rebuild one company's page from whatever is already stored. Free, and
    never touches a model — this is what the companies that were not gathered
    for get on every run."""
    stored = store.load(company)
    # Topics come from the live config, never from the stored copy: they are
    # just URLs built from the keywords, so rebuilding costs nothing and a
    # stored copy would keep linking to a keyword that has since been edited.
    topics = build_launcher(company.cfg)[0]
    posts = (stored or {}).get("posts") or []
    if not (company.cfg.get("search") or {}).get("find_posts", False):
        posts = []
    _note_stale(company, stored)
    render(
        posts,
        company,
        warnings=(stored or {}).get("warnings") or [],
        topics=topics,
        usage=(stored or {}).get("usage") or {},
        gathered_at=(stored or {}).get("generated_at"),
        mark_new=False,
        endpoint=endpoint,
        siblings=companies,
    )


def _work(company, mode: str, no_gather: bool, usage: Usage, now: str) -> tuple:
    """Do the paid stage for one company. Returns (posts, topics, warnings,
    gathered_at, mode) — mode is returned because it can be downgraded."""
    stored = store.load(company)
    warnings: list[str] = []
    # Cleared if the run fails to produce posts for the current settings, so
    # the fingerprint is not stamped as satisfied and --auto tries again.
    config_applied = True

    if mode == "auto":
        mode, reason = store.decide_mode(company.cfg, stored)
        print(f"Auto mode: {mode} — {reason}.")
    elif mode in (store.REDRAFT, store.RENDER) and stored is None:
        # Render and redraft are advertised as free and as "no searching". A
        # company with nothing stored yet has nothing to render or redraft, and
        # the honest answer is to say so — NOT to quietly upgrade to a paid
        # search the operator did not pick. Only --auto may choose to gather.
        print(f"::warning::Nothing stored for {company.slug} yet — {mode} has nothing to work from.")
        warnings.append(
            f"No posts have been gathered for {company.name} yet, so there was nothing to "
            f"{mode}. The topic links below work already — press Refresh to search for posts."
        )
        mode = store.RENDER

    # Neither searching nor re-drafting happens as a side effect of editing a
    # config file — both spend real money, and only the Refresh button asks for
    # that. A redraft is ~$0.42 and used to slip through this guard, which
    # covered gathering only; nothing in the spend caps could see it.
    if no_gather and mode in (store.GATHER, store.REDRAFT):
        if stored is None:
            print("::warning::No stored data yet, and gathering was not requested.")
            warnings.append(
                "No posts have been gathered yet. Use the Refresh button above to run "
                "the first search."
            )
        # Nothing to print here: the RENDER branch below calls _note_stale,
        # which says the same thing and names the company. Log only either
        # way — this condition holds from the moment a config is edited until
        # someone pays for a Refresh, so on the page it was permanent furniture
        # rather than news.
        mode = store.RENDER

    # A gather against template copy pays full price for posts judged against a
    # company that does not exist yet — expensive AND it looks like it worked.
    if mode in (store.GATHER, store.REDRAFT) and company.is_placeholder():
        print(f"::warning::{company.slug}: profile/voice is still the template — not spending.")
        warnings.append(
            f"{company.name}'s profile and voice are still the template, so nothing was "
            f"searched for or written. Fill in {company.config_rel} — replace every "
            f"{registry.PLACEHOLDER} — and press Refresh."
        )
        mode = store.RENDER

    if mode == store.GATHER:
        # 1. The per-topic deep links into LinkedIn's own search. Free.
        topics, w = build_launcher(company.cfg)
        warnings += w
        print(f"Prepared {len(topics)} live search links.")

        # 2. Searching for individual posts is optional. See find_posts in the
        #    company's config file.
        if (company.cfg.get("search") or {}).get("find_posts", False):
            search_posts, source = _search_provider(company.cfg)
            print(f"Searching for posts via {source}.")
            try:
                posts, w = search_posts(company.cfg, usage=usage)
                warnings += w
                print(f"{len(posts)} posts passed the date and keyword filters.")
                posts, w = enrich_posts(posts, company.cfg, usage=usage)
                warnings += w
                gathered_at = now
            except SearchFailed as exc:
                # Keep what we already have. Treating a failed fetch as an
                # empty result would replace good posts with nothing and then
                # tell the user they were "gathered just now".
                print(f"::warning::Search failed: {exc}")
                posts = (stored or {}).get("posts") or []
                gathered_at = (stored or {}).get("generated_at") or now
                config_applied = False
                warnings.append(
                    f"Could not fetch posts ({exc}) "
                    + (f"— showing the {len(posts)} from the previous search."
                       if posts else "— no previous posts to fall back on.")
                )
        else:
            posts = []
            gathered_at = now
            print("Post search is off (search.find_posts) — links only.")

    elif mode == store.REDRAFT:
        posts = stored.get("posts") or []
        gathered_at = stored.get("generated_at") or now
        print(f"Re-scoring {len(posts)} stored posts — no searching.")
        for post in posts:  # drop the old judgement so it is genuinely redone
            post.pop("relevance", None)
            post.pop("reason", None)
            post.pop("comments", None)
        topics, w = build_launcher(company.cfg)
        warnings += w
        posts, w = enrich_posts(posts, company.cfg, usage=usage)
        warnings += w

    else:  # RENDER — stored may be absent if nothing has been gathered yet
        posts = (stored or {}).get("posts") or []
        warnings += (stored or {}).get("warnings") or []
        # None, not now(): a company that has never been gathered for must not
        # have its page claim the posts were fetched a moment ago.
        gathered_at = (stored or {}).get("generated_at")
        # Rebuilt from the live config rather than replayed from storage, so an
        # edited keyword updates its LinkedIn link immediately. Free either way.
        topics, w = build_launcher(company.cfg)
        warnings += w
        _note_stale(company, stored)
        print(f"Rendering {len(posts)} stored posts — no API calls.")

    # With post search off, previously-gathered posts must not keep rendering:
    # they are exactly the stale material that turning it off was meant to stop
    # showing. This applies in every mode, including a plain re-render.
    if not (company.cfg.get("search") or {}).get("find_posts", False) and posts:
        print(f"Post search is off — hiding {len(posts)} previously gathered posts.")
        posts = []

    return posts, topics, warnings, gathered_at, mode, stored, config_applied


def main() -> int:
    try:
        reg = registry.registry()
        all_companies = registry.load_all(reg)
    except registry.ConfigError as exc:
        print(f"::error::{exc}")
        return 1
    endpoint = (reg.get("refresh_endpoint") or "").strip()

    if "--sample" in sys.argv:
        return _run_sample(all_companies, endpoint)

    mode = _mode_from_argv(sys.argv)
    no_gather = "--no-gather" in sys.argv

    # Before the --company guard below: with no key nothing can spend anyway,
    # and the point of this branch is that a bare run still produces a page
    # telling an admin what is missing.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("::warning::ANTHROPIC_API_KEY not set — rendering setup page.")
        for company in all_companies:
            render(
                [], company, warnings=[], configured=False,
                topics=build_launcher(company.cfg)[0],
                endpoint=endpoint, siblings=all_companies,
                mark_new=False, persist_seen=False,
            )
        return 0

    try:
        wanted = _company_from_argv(sys.argv)
        # Paid work must name its company. Defaulting would let a bare
        # --gather spend on whichever company happened to be first, or on all
        # of them, neither of which anyone asked for.
        if wanted is None and len(all_companies) > 1 and not no_gather and mode != store.RENDER:
            slugs = ", ".join(c.slug for c in all_companies)
            print(f"::error::--{mode} spends money, so it needs --company. Choose one of: {slugs}.")
            return 1
        target = registry.select(all_companies, wanted)
    except registry.ConfigError as exc:
        print(f"::error::{exc}")
        return 1

    usage = Usage()
    now = datetime.now(timezone.utc).isoformat()

    print(f"── {target.name} ({target.slug}) ──")
    posts, topics, warnings, gathered_at, mode, stored, config_applied = _work(
        target, mode, no_gather, usage, now
    )

    # Render first so the is_new flags it sets get persisted, but save in a
    # finally: a rendering bug must not throw away searches we already paid
    # for. Re-running with --render then rebuilds the page for free.
    try:
        render(
            posts,
            target,
            warnings=warnings,
            configured=True,
            topics=topics,
            usage=usage.summary() if mode != store.RENDER else ((stored or {}).get("usage") or {}),
            gathered_at=gathered_at,
            mark_new=(mode == store.GATHER),
            endpoint=endpoint,
            siblings=all_companies,
        )
    finally:
        if mode != store.RENDER:
            merged = usage.summary()
            if mode == store.REDRAFT:
                previous = (stored or {}).get("usage") or {}
                for key in ("external",):
                    if previous.get(key) and not merged.get(key):
                        merged[key] = previous[key]
                        merged["usd"] = round(
                            merged.get("usd", 0)
                            + sum(e.get("usd", 0) for e in previous[key].values()), 4)
            store.save(
                target,
                topics=topics,
                posts=posts,
                warnings=warnings,
                usage=merged,
                generated_at=gathered_at,
                config_applied=config_applied,
            )
            print(f"Stored run data in {target.data_path.relative_to(ROOT)}")

    # Every other company's page is rebuilt from what it already had. Free, and
    # required: a Pages deploy replaces the entire site, so a run that published
    # only the gathered company would 404 the rest.
    for company in all_companies:
        if company.slug != target.slug:
            print(f"── {company.name} ({company.slug}) — re-rendering from stored data ──")
            _render_stored(company, endpoint, all_companies)

    print()
    print(usage.report())
    print()
    for w in warnings:
        print(f"::warning::{w}")
    for company in all_companies:
        print(f"Dashboard written to {company.site_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
