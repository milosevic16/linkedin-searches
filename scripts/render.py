"""Render the dashboard: site/index.html + site/data.json.

The page is organised by TOPIC. Each topic gets a deep link into LinkedIn's
own search plus reusable comment angles to bring to whatever it turns up.

When search.find_posts is on, individual posts found for a topic appear
underneath it, each with three drafts written for that specific post. It is
off by default: web search sees LinkedIn months late, so what it finds is
never fresh enough to be worth commenting on. The angles are general because
we cannot read the posts behind the live link — LinkedIn blocks crawlers.

site/data.json carries the URLs already shown, fetched back from the live
Pages deploy on the next run so repeats aren't re-flagged as new.
"""

from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

SEEN_CAP = 1500

try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo("Europe/Ljubljana")
except Exception:  # pragma: no cover
    _TZ = timezone.utc


def _pages_base_url() -> str:
    explicit = os.environ.get("PAGES_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}"
    return ""


def _load_previous_seen() -> list[str] | None:
    """URLs already shown, or None if the history could not be read.

    The distinction matters: an empty list is written back as the new
    history, so returning [] on a failed fetch silently erases everything
    ever shown and re-flags every post as NEW.
    """
    base = _pages_base_url()
    if not base:
        return []
    try:
        resp = requests.get(f"{base}/data.json", timeout=10)
    except Exception:
        return None
    if resp.status_code == 404:
        return []          # first deploy: genuinely nothing seen yet
    if resp.status_code != 200:
        return None
    try:
        return [u for u in resp.json().get("seen", []) if isinstance(u, str)]
    except Exception:
        return None


def _esc(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


def _age_label(post: dict) -> str:
    """How old the post is, in words. With a 48-hour window this is the single
    most decision-relevant fact on a card: engagement decays fast enough that
    a 40-hour-old post is a different proposition from a 2-hour-old one."""
    hours = post.get("age_hours")
    if not isinstance(hours, (int, float)):
        return ""
    if hours < 1:
        return "just now"
    if hours < 2:
        return "1 hour ago"
    if hours < 24:
        return f"{int(hours)} hours ago"
    days = hours / 24
    return "yesterday" if days < 2 else f"{int(days)} days ago"


def _safe_url(url: str) -> str:
    """Only http(s) links reach the page. Post URLs and anything a model
    produced are untrusted, and a javascript: URL would otherwise render as a
    live link in the user's own page origin."""
    text = str(url or "").strip()
    return text if text.lower().startswith(("https://", "http://")) else "#"


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in str(text or "")]
    return "".join(keep).strip("-")[:60] or "topic"


_SCORE_CLASS = lambda r: "s-high" if r >= 8 else ("s-mid" if r >= 5 else "s-low")

_STYLE_LABEL = {"insight": "Insight", "question": "Question", "experience": "Experience"}


def _drafts_block(items: list[dict], summary: str, open_by_default: bool) -> str:
    """Shared markup for per-post drafts and per-topic angles."""
    if not items:
        return ""
    rows = "".join(
        f"""<div class="draft">
          <div class="draft-head"><span class="draft-style">{_esc(label)}</span>
          <button class="copy" type="button">Copy</button></div>
          <p class="draft-text">{_esc(text)}</p>
        </div>"""
        for label, text in items
    )
    return f"""<details class="drafts"{" open" if open_by_default else ""}>
      <summary>{_esc(summary)}</summary>{rows}</details>"""


def _card(post: dict) -> str:
    rel = post.get("relevance")
    score = (
        f'<span class="score {_SCORE_CLASS(rel)}" title="Relevance {rel}/10">{rel}</span>'
        if isinstance(rel, int)
        else '<span class="score s-none" title="Not AI-scored">–</span>'
    )
    author = _esc(post.get("author") or "Unknown author")
    kind = "Article" if post.get("is_article") else "Post"
    age = _age_label(post)
    age_html = f' <span class="age">· {_esc(age)}</span>' if age else ""
    new_chip = '<span class="chip chip-new">NEW</span>' if post.get("is_new") else ""
    # Only the *other* topics this post matched — its own section is implied.
    extra = [k for k in (post.get("keywords") or [])[1:]]
    chips = "".join(f'<span class="chip">also: {_esc(k)}</span>' for k in extra)
    reason = post.get("reason")
    reason_html = f'<p class="reason">{_esc(reason)}</p>' if reason else ""

    comments = [
        (_STYLE_LABEL.get(c.get("style"), "Idea"), c.get("text"))
        for c in (post.get("comments") or [])
    ]
    drafts = _drafts_block(
        comments,
        f"{len(comments)} comment{'s' if len(comments) != 1 else ''} written for this post",
        open_by_default=False,
    )
    if not comments:
        drafts = '<p class="nodraft">No drafts for this post — it fell outside this run\'s drafting budget.</p>'

    return f"""<article class="card" data-new="{1 if post.get("is_new") else 0}">
      <div class="card-head">
        {score}
        <div class="card-id">
          <div class="author">{author} <span class="kind">· {kind}</span>{age_html}</div>
          <div class="title">{_esc(post.get("title") or post.get("url"))}</div>
        </div>
        <a class="open" href="{_esc(_safe_url(post.get("url")))}" target="_blank" rel="noopener">Open&nbsp;↗</a>
      </div>
      <p class="snippet">{_esc(post.get("snippet"))}</p>
      {reason_html}
      <div class="chips">{new_chip}{chips}</div>
      {drafts}
    </article>"""


def _group_by_topic(posts: list[dict], topics: list[dict]) -> dict[str, list[dict]]:
    """Each post belongs to ONE topic — its first matched keyword — so a post
    matching three keywords doesn't render three times. The other matches show
    as chips on the card."""
    known = {t["keyword"] for t in topics}
    grouped: dict[str, list[dict]] = {t["keyword"]: [] for t in topics}
    for post in posts:
        keywords = post.get("keywords") or []
        primary = next((k for k in keywords if k in known), None)
        grouped.setdefault(primary or "Other matches", []).append(post)
    return grouped


def _topic_section(topic: dict, posts: list[dict], min_rel: int,
                   searching: bool = True, index: int = 0) -> str:
    angles = [(a.get("label") or "Angle", a.get("text")) for a in topic.get("angles") or []]
    angles_html = _drafts_block(
        angles,
        f"{len(angles)} general angles — for the posts behind the link above, which we cannot read",
        open_by_default=False,
    )

    def sort_key(p: dict):
        rel = p.get("relevance")
        return (-(rel if isinstance(rel, int) else -1), 0 if p.get("is_new") else 1)

    ordered = sorted(posts, key=sort_key)
    drafted = [p for p in ordered if p.get("comments")]
    top = [p for p in ordered if not isinstance(p.get("relevance"), int) or p["relevance"] >= min_rel]
    rest = [p for p in ordered if p not in top]

    if top:
        cards = "".join(_card(p) for p in top)
    elif searching:
        cards = """<p class="empty-topic">Nothing recent enough found for this topic.
        Use the link above — it queries LinkedIn directly and is always current.</p>"""
    else:
        cards = ""  # search is off by design; the links and angles are the section

    rest_html = ""
    if rest:
        rest_html = f"""<details class="lowrel">
          <summary>Lower relevance ({len(rest)})</summary>{"".join(_card(p) for p in rest)}</details>"""

    n_new = sum(1 for p in posts if p.get("is_new"))
    if not searching and not posts:
        count_bits = ["Opens LinkedIn's own search — always current"]
    else:
        count_bits = [f"{len(posts)} post{'s' if len(posts) != 1 else ''} found"]
        if drafted:
            count_bits.append(f"{len(drafted)} with drafts")
        if n_new:
            count_bits.append(f"{n_new} new")

    return f"""<section class="topic-block" id="t-{index}-{_slug(topic['keyword'])}" data-new="{1 if n_new else 0}">
      <div class="topic-head">
        <div class="topic-id">
          <h2 class="topic-h">{_esc(topic['keyword'])}</h2>
          <p class="topic-meta">{_esc(" · ".join(count_bits))}</p>
        </div>
        <div class="topic-links">
          <a class="open" href="{_esc(_safe_url(topic['fresh_url']))}" target="_blank" rel="noopener">{_esc(topic['window_label'])}&nbsp;↗</a>
          <a class="open alt" href="{_esc(_safe_url(topic['week_url']))}" target="_blank" rel="noopener">Past week&nbsp;↗</a>
        </div>
      </div>
      {angles_html}
      <div class="cards">{cards}</div>
      {rest_html}
    </section>"""


def _setup_section() -> str:
    return """<section class="notice setup">
      <h2>Almost there — one-time setup needed</h2>
      <p>The dashboard ran, but its API key is not configured yet. An admin needs to add one
      <strong>repository secret</strong> (Settings → Secrets and variables → Actions):</p>
      <ol>
        <li><code>ANTHROPIC_API_KEY</code> — finds the posts, scores them, and drafts the comments</li>
      </ol>
      <p>Get one at <a href="https://console.anthropic.com/">console.anthropic.com</a> → API keys.
      Full instructions are in the repository README. Once the secret is in place, the next
      scheduled run fills this page with posts.</p>
    </section>"""


def render(
    posts: list[dict],
    cfg: dict,
    warnings: list[str],
    configured: bool = True,
    topics: list[dict] | None = None,
    usage: dict | None = None,
    gathered_at: str | None = None,
    mark_new: bool = True,
) -> None:
    searching = bool((cfg.get("search") or {}).get("find_posts", False))
    site = Path(__file__).resolve().parent.parent / "site"
    site.mkdir(exist_ok=True)
    topics = topics or []

    # Always load the history — it gets written back below, so skipping the
    # load would silently truncate it on a render-only rebuild. Only the
    # is_new *decision* is skipped: on a rebuild the posts are unchanged, and
    # re-deciding would mark everything as already-seen and drop the flags.
    loaded = _load_previous_seen()
    history_readable = loaded is not None
    prev_seen = loaded or []
    seen_set = set(prev_seen)
    if not history_readable:
        warnings = list(warnings) + [
            "Could not read which posts were shown last time, so NEW badges are omitted "
            "this run. The history itself is untouched."
        ]
    elif mark_new:
        for p in posts:
            p["is_new"] = p.get("url") not in seen_set

    now = datetime.now(_TZ)
    generated = now.strftime("%A, %d %b %Y · %H:%M")
    repo = os.environ.get("GITHUB_REPOSITORY", "milosevic16/linkedin-searches")

    # Now that refreshing is manual, how old the posts are is the single most
    # useful thing on the page — it is what tells you whether to spend on a
    # refresh. Always show it, in plain language.
    freshness = "Posts have never been gathered"
    if gathered_at:
        try:
            when = datetime.fromisoformat(gathered_at).astimezone(_TZ)
            hours = (now - when).total_seconds() / 3600
            stamp = when.strftime("%d %b, %H:%M")
            if hours < 1:
                freshness = f"Posts gathered just now ({stamp})"
            elif hours < 24:
                freshness = f"Posts gathered {int(hours)}h ago ({stamp})"
            else:
                days = int(hours // 24)
                freshness = f"Posts gathered {days} day{'s' if days != 1 else ''} ago ({stamp})"
        except ValueError:
            freshness = "Posts gathered at an unknown time"

    min_rel = int(cfg.get("min_relevance", 5))
    n_new = sum(1 for p in posts if p.get("is_new"))
    n_drafted = sum(1 for p in posts if p.get("comments"))

    warn_html = ""
    if warnings:
        items = "".join(f"<li>{_esc(w)}</li>" for w in warnings)
        warn_html = f'<section class="notice warn"><ul>{items}</ul></section>'

    if not configured:
        body_main = _setup_section()
    else:
        grouped = _group_by_topic(posts, topics)
        blocks = [
            _topic_section(t, grouped.get(t["keyword"], []), min_rel, searching, i)
            for i, t in enumerate(topics)
        ]
        leftovers = grouped.get("Other matches") or []
        if leftovers:
            blocks.append(
                f"""<section class="topic-block" id="t-other">
                  <div class="topic-head"><div class="topic-id">
                    <h2 class="topic-h">Other matches</h2>
                    <p class="topic-meta">Found under keywords no longer in config.yml</p>
                  </div></div>
                  <div class="cards">{"".join(_card(p) for p in leftovers)}</div>
                </section>"""
            )
        nav = "".join(
            f'<a class="navchip" href="#t-{i}-{_slug(t["keyword"])}">{_esc(t["keyword"])}</a>'
            for i, t in enumerate(topics)
        )
        body_main = f"""<nav class="topicnav">{nav}</nav>
        <div class="filters">
          <button class="fchip active" data-filter="all" type="button">All topics</button>
          <button class="fchip" data-filter="new" type="button">Only topics with new posts</button>
        </div>
        {"".join(blocks)}"""

    if searching or posts:
        stats = f"""<div class="stats">
          <div class="stat"><span class="stat-n">{len(topics)}</span><span class="stat-l">topics</span></div>
          <div class="stat"><span class="stat-n">{len(posts)}</span><span class="stat-l">posts found</span></div>
          <div class="stat"><span class="stat-n">{n_drafted}</span><span class="stat-l">with drafts</span></div>
          <div class="stat"><span class="stat-n">{n_new}</span><span class="stat-l">new in last gather</span></div>
        </div>"""
    else:
        n_angles = sum(len(t.get("angles") or []) for t in topics)
        stats = f"""<div class="stats">
          <div class="stat"><span class="stat-n">{len(topics)}</span><span class="stat-l">topics to work</span></div>
          <div class="stat"><span class="stat-n">{n_angles}</span><span class="stat-l">comment angles</span></div>
        </div>"""

    # The page is static HTML on a public site, so it holds no credentials.
    # The password is typed by the visitor and checked by a proxy that owns
    # the GitHub token; nothing secret is ever served here. Without an
    # endpoint configured, fall back to linking at the Actions page.
    endpoint = (cfg.get("refresh_endpoint") or "").strip()
    if endpoint:
        refresh_html = f"""<section class="refresh">
          <div class="refresh-text">
            <strong>Posts are only searched for when you ask.</strong>
            <span>Finds posts from the last 48 hours and writes fresh drafts.
            Takes about 5 minutes and costs roughly EUR 0.35.</span>
          </div>
          <form class="refresh-form" id="refresh-form" autocomplete="off">
            <input class="refresh-pw" id="refresh-pw" type="password"
                   placeholder="Password" aria-label="Refresh password" required>
            <button class="refresh-btn" id="refresh-go" type="submit">Refresh</button>
          </form>
        </section>
        <p class="refresh-status" id="refresh-status" role="status" aria-live="polite"></p>"""
    else:
        refresh_html = f"""<section class="refresh">
          <div class="refresh-text">
            <strong>Posts are only searched for when you ask.</strong>
            <span>One-click refresh is not set up yet — see worker/README.md.
            For now this opens the Actions page.</span>
          </div>
          <a class="refresh-btn" href="https://github.com/{_esc(repo)}/actions/workflows/dashboard.yml"
             target="_blank" rel="noopener">Refresh posts&nbsp;↗</a>
        </section>"""

    cost_note = ""
    if usage and usage.get("usd"):
        cost_note = (
            f'This run cost about USD {usage["usd"]:.2f} '
            f'(≈ EUR {usage.get("eur", 0):.2f}) in API usage across '
            f'{usage.get("calls", 0)} calls and {usage.get("searches", 0)} web searches.<br>'
        )
    elif usage is not None and not usage.get("usd"):
        cost_note = "This page was rebuilt from stored data — no API calls, no cost.<br>"

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>LinkedIn Comment Radar</title>
<style>
:root {{
  --bg: #f6f4ef; --surface: #ffffff; --ink: #1f2a33; --ink-2: #52616d; --ink-3: #8a96a0;
  --line: #e3ded4; --accent: #0a66c2; --accent-ink: #ffffff;
  --good-bg: #e2f2e5; --good-ink: #1e6b34; --mid-bg: #e4edf7; --mid-ink: #275e93;
  --low-bg: #eceae4; --low-ink: #6e6a5e; --warn-bg: #fdf3e0; --warn-line: #ecd9ae;
  --new-bg: #0a66c2; --new-ink: #fff; --shadow: 0 1px 3px rgba(31,42,51,.08);
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #14191f; --surface: #1d242c; --ink: #e8ecef; --ink-2: #aab6bf; --ink-3: #77828c;
    --line: #2c353f; --accent: #6cb2f0; --accent-ink: #0d1b2a;
    --good-bg: #1d3524; --good-ink: #8fd8a0; --mid-bg: #1e3046; --mid-ink: #9cc6ef;
    --low-bg: #262c33; --low-ink: #9aa4ad; --warn-bg: #35301f; --warn-line: #55492a;
    --new-bg: #6cb2f0; --new-ink: #0d1b2a; --shadow: none;
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}}
.wrap {{ max-width: 820px; margin: 0 auto; padding: 24px 16px 64px; }}
header h1 {{ font-size: 22px; margin: 0 0 2px; letter-spacing: -.01em; }}
.sub {{ color: var(--ink-2); font-size: 13px; margin: 0 0 18px; }}
.stats {{ display: flex; gap: 10px; margin: 0 0 18px; flex-wrap: wrap; }}
.stat {{ background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  padding: 8px 14px; display: flex; flex-direction: column; min-width: 92px; box-shadow: var(--shadow); }}
.stat-n {{ font-size: 20px; font-weight: 650; }}
.stat-l {{ font-size: 12px; color: var(--ink-2); }}
.freshness {{ margin: -12px 0 18px; font-size: 13px; font-weight: 600; color: var(--ink-2); }}
.refresh {{ display: flex; gap: 14px; align-items: center; flex-wrap: wrap;
  background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--accent);
  border-radius: 12px; padding: 14px 16px; margin-bottom: 8px; box-shadow: var(--shadow); }}
.refresh-text {{ flex: 1; min-width: 240px; display: flex; flex-direction: column; gap: 2px; }}
.refresh-text strong {{ font-size: 14px; }}
.refresh-text span {{ font-size: 12.5px; color: var(--ink-2); }}
.refresh-btn {{ flex: none; background: var(--accent); color: var(--accent-ink); text-decoration: none;
  border: none; border-radius: 8px; padding: 10px 18px; font-size: 14px; font-weight: 650;
  white-space: nowrap; cursor: pointer; font-family: inherit; }}
.refresh-btn[disabled] {{ opacity: .55; cursor: progress; }}
.refresh-form {{ display: flex; gap: 8px; flex: none; }}
.refresh-pw {{ width: 130px; border: 1px solid var(--line); border-radius: 8px; padding: 9px 12px;
  font-size: 14px; font-family: inherit; background: var(--bg); color: var(--ink); }}
.refresh-pw:focus {{ outline: 2px solid var(--accent); outline-offset: 1px; }}
.refresh-status {{ margin: 8px 2px 18px; font-size: 13px; font-weight: 600; min-height: 1.2em; }}
.refresh-status.err {{ color: #b3261e; }}
.refresh-status.ok {{ color: var(--good-ink); }}
.topicnav {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 0 0 14px;
  padding-bottom: 14px; border-bottom: 1px solid var(--line); }}
.navchip {{ font-size: 12.5px; color: var(--ink-2); text-decoration: none;
  border: 1px solid var(--line); border-radius: 999px; padding: 4px 11px; background: var(--surface); }}
.navchip:hover {{ color: var(--accent); border-color: var(--accent); }}
.filters {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 0 0 20px; }}
.fchip {{ background: var(--surface); color: var(--ink-2); border: 1px solid var(--line);
  border-radius: 999px; padding: 5px 12px; font-size: 13px; cursor: pointer; }}
.fchip.active {{ background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }}
.topic-block {{ margin: 0 0 34px; scroll-margin-top: 12px; }}
.topic-block.hidden {{ display: none; }}
.topic-head {{ display: flex; gap: 12px; align-items: flex-start; flex-wrap: wrap;
  margin-bottom: 10px; padding-bottom: 10px; border-bottom: 2px solid var(--line); }}
.topic-id {{ flex: 1; min-width: 220px; }}
.topic-h {{ font-size: 17px; margin: 0; letter-spacing: -.01em; }}
.topic-meta {{ color: var(--ink-2); font-size: 12.5px; margin: 3px 0 0; }}
.topic-links {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.cards {{ display: flex; flex-direction: column; gap: 14px; }}
.card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
  padding: 14px 16px; box-shadow: var(--shadow); }}
.card-head {{ display: flex; gap: 12px; align-items: flex-start; }}
.card-id {{ flex: 1; min-width: 0; }}
.score {{ flex: none; width: 34px; height: 34px; border-radius: 9px; display: grid; place-items: center;
  font-weight: 700; font-size: 15px; }}
.s-high {{ background: var(--good-bg); color: var(--good-ink); }}
.s-mid  {{ background: var(--mid-bg);  color: var(--mid-ink); }}
.s-low, .s-none {{ background: var(--low-bg); color: var(--low-ink); }}
.author {{ font-weight: 620; font-size: 14px; }}
.kind {{ color: var(--ink-3); font-weight: 400; }}
.age {{ color: var(--accent); font-weight: 600; font-size: 12.5px; }}
.title {{ color: var(--ink-2); font-size: 13px; overflow: hidden; text-overflow: ellipsis;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
.open {{ flex: none; background: var(--accent); color: var(--accent-ink); text-decoration: none;
  border-radius: 8px; padding: 6px 12px; font-size: 13px; font-weight: 600; white-space: nowrap; }}
.open.alt {{ background: transparent; color: var(--accent); border: 1px solid var(--accent); }}
.snippet {{ margin: 10px 0 6px; color: var(--ink); font-size: 14px; }}
.reason {{ margin: 0 0 8px; color: var(--ink-2); font-size: 13px; font-style: italic; }}
.chips {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 4px; }}
.chip {{ font-size: 11.5px; color: var(--ink-2); border: 1px solid var(--line);
  border-radius: 999px; padding: 2px 9px; }}
.chip-new {{ background: var(--new-bg); color: var(--new-ink); border-color: var(--new-bg); font-weight: 700; }}
.drafts {{ margin-top: 8px; border-top: 1px dashed var(--line); padding-top: 8px; }}
.drafts summary {{ cursor: pointer; color: var(--accent); font-size: 13.5px; font-weight: 600; }}
.topic-block > .drafts {{ margin: 0 0 14px; border-top: none; padding-top: 0; }}
.draft {{ background: var(--bg); border: 1px solid var(--line); border-radius: 10px;
  padding: 10px 12px; margin-top: 10px; }}
.draft-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }}
.draft-style {{ font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--ink-3); font-weight: 700; }}
.copy {{ background: transparent; border: 1px solid var(--line); color: var(--ink-2);
  border-radius: 7px; padding: 3px 10px; font-size: 12px; cursor: pointer; }}
.copy.done {{ color: var(--good-ink); border-color: var(--good-ink); }}
.draft-text {{ margin: 0; font-size: 14px; white-space: pre-wrap; }}
.nodraft, .empty-topic {{ color: var(--ink-3); font-size: 13px; margin: 8px 0 0; font-style: italic; }}
.empty-topic {{ padding: 10px 2px; }}
.lowrel {{ margin-top: 14px; }}
.lowrel summary {{ cursor: pointer; color: var(--ink-2); font-size: 13.5px; }}
.lowrel .card {{ margin-top: 12px; opacity: .85; }}
.notice {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
  padding: 16px 18px; margin-bottom: 16px; }}
.notice h2 {{ margin: 0 0 8px; font-size: 17px; }}
.notice.warn {{ background: var(--warn-bg); border-color: var(--warn-line); font-size: 13.5px; }}
.notice.warn ul {{ margin: 0; padding-left: 18px; }}
footer {{ margin-top: 36px; color: var(--ink-3); font-size: 12px; text-align: center; }}
footer a {{ color: var(--ink-3); }}
code {{ background: var(--low-bg); border-radius: 5px; padding: 1px 5px; font-size: 13px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>LinkedIn Comment Radar</h1>
    <p class="sub">Your LinkedIn commenting shortlist</p>
    <p class="freshness">{_esc(freshness)}</p>
  </header>
  {stats}
  {refresh_html}
  {warn_html}
  {body_main}
  <footer>{cost_note}Adapt every draft before posting — identical comments from two accounts read as bots.<br>
  Topics and voice live in <a href="https://github.com/{_esc(repo)}/blob/main/config.yml">config.yml</a>.</footer>
</div>
<script>
const REFRESH_ENDPOINT = {json.dumps(endpoint)};
const form = document.getElementById("refresh-form");
if (form) {{
  const pw = document.getElementById("refresh-pw");
  const go = document.getElementById("refresh-go");
  const out = document.getElementById("refresh-status");
  const say = (msg, kind) => {{ out.textContent = msg; out.className = "refresh-status " + (kind || ""); }};
  form.addEventListener("submit", async (e) => {{
    e.preventDefault();
    go.disabled = true;
    say("Starting…");
    try {{
      const res = await fetch(REFRESH_ENDPOINT, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ password: pw.value }}),
      }});
      const data = await res.json().catch(() => ({{}}));
      if (res.ok) {{
        say(data.message || "Refresh started. Reload this page in about 5 minutes.", "ok");
        pw.value = "";
      }} else {{
        say(data.error || ("Could not start the refresh (" + res.status + ")."), "err");
      }}
    }} catch (err) {{
      say("Could not reach the refresh service. Check your connection.", "err");
    }} finally {{
      go.disabled = false;
    }}
  }});
}}
document.addEventListener("click", (e) => {{
  const copyBtn = e.target.closest(".copy");
  if (copyBtn) {{
    const text = copyBtn.closest(".draft").querySelector(".draft-text").textContent;
    navigator.clipboard.writeText(text).then(() => {{
      copyBtn.textContent = "Copied ✓"; copyBtn.classList.add("done");
      setTimeout(() => {{ copyBtn.textContent = "Copy"; copyBtn.classList.remove("done"); }}, 1600);
    }});
    return;
  }}
  const chip = e.target.closest(".fchip");
  if (chip) {{
    document.querySelectorAll(".fchip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    const onlyNew = chip.dataset.filter === "new";
    document.querySelectorAll(".topic-block").forEach((block) => {{
      block.classList.toggle("hidden", onlyNew && block.dataset.new !== "1");
    }});
  }}
}});
</script>
</body>
</html>"""

    (site / "index.html").write_text(page, encoding="utf-8")

    # Persist state for the next run: everything ever shown, capped.
    merged_seen = list(prev_seen)
    if history_readable:
        for post in posts:
            url = post.get("url")
            if url and url not in seen_set:
                seen_set.add(url)
                merged_seen.append(url)
        if len(merged_seen) > SEEN_CAP:
            merged_seen = merged_seen[-SEEN_CAP:]
    (site / "data.json").write_text(
        json.dumps(
            {
                # Deliberately the gather time, not now(): this file is
                # published on every render, and a fresh timestamp would make
                # identical data deploy as a different byte stream every time.
                "generated_at": gathered_at or now.isoformat(),
                "seen": merged_seen,
                "posts": [
                    {k: p.get(k) for k in ("url", "author", "title", "relevance", "keywords")}
                    for p in posts
                ],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
