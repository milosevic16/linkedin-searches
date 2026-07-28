"""Render the dashboard: site/index.html + site/data.json.

The previous deploy's data.json (fetched from the live GitHub Pages URL)
is used to remember which post URLs were already shown, so repeats from
overlapping search windows don't get flagged as new again.
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


def _load_previous_seen() -> list[str]:
    base = _pages_base_url()
    if not base:
        return []
    try:
        resp = requests.get(f"{base}/data.json", timeout=10)
        if resp.status_code == 200:
            seen = resp.json().get("seen", [])
            return [u for u in seen if isinstance(u, str)]
    except Exception:
        pass
    return []


def _esc(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


_SCORE_CLASS = lambda r: "s-high" if r >= 8 else ("s-mid" if r >= 5 else "s-low")

_STYLE_LABEL = {"insight": "Insight", "question": "Question", "experience": "Experience"}


def _card(post: dict) -> str:
    rel = post.get("relevance")
    score = (
        f'<span class="score {_SCORE_CLASS(rel)}" title="Relevance {rel}/10">{rel}</span>'
        if isinstance(rel, int)
        else '<span class="score s-none" title="Not AI-scored">–</span>'
    )
    author = _esc(post.get("author") or "Unknown author")
    kind = "Article" if post.get("is_article") else "Post"
    chips = "".join(f'<span class="chip">{_esc(k)}</span>' for k in post.get("keywords") or [])
    new_chip = '<span class="chip chip-new">NEW</span>' if post.get("is_new") else ""
    reason = post.get("reason")
    reason_html = f'<p class="reason">{_esc(reason)}</p>' if reason else ""

    drafts = ""
    comments = post.get("comments") or []
    if comments:
        items = "".join(
            f"""<div class="draft">
              <div class="draft-head"><span class="draft-style">{_esc(_STYLE_LABEL.get(c.get("style"), "Idea"))}</span>
              <button class="copy" type="button">Copy</button></div>
              <p class="draft-text">{_esc(c.get("text"))}</p>
            </div>"""
            for c in comments
        )
        drafts = f"""<details class="drafts">
          <summary>{len(comments)} comment idea{"s" if len(comments) != 1 else ""}</summary>
          {items}
        </details>"""

    kws = "|".join(post.get("keywords") or [])
    return f"""<article class="card" data-kws="{_esc(kws)}" data-new="{1 if post.get("is_new") else 0}">
      <div class="card-head">
        {score}
        <div class="card-id">
          <div class="author">{author} <span class="kind">· {kind}</span></div>
          <div class="title">{_esc(post.get("title") or post.get("url"))}</div>
        </div>
        <a class="open" href="{_esc(post.get("url"))}" target="_blank" rel="noopener">Open&nbsp;↗</a>
      </div>
      <p class="snippet">{_esc(post.get("snippet"))}</p>
      {reason_html}
      <div class="chips">{new_chip}{chips}</div>
      {drafts}
    </article>"""


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


def render(posts: list[dict], cfg: dict, warnings: list[str], configured: bool = True) -> None:
    site = Path(__file__).resolve().parent.parent / "site"
    site.mkdir(exist_ok=True)

    prev_seen = _load_previous_seen()
    seen_set = set(prev_seen)
    for p in posts:
        p["is_new"] = p["url"] not in seen_set

    now = datetime.now(_TZ)
    generated = now.strftime("%A, %d %b %Y · %H:%M")

    min_rel = int(cfg.get("min_relevance", 5))
    any_scored = any(isinstance(p.get("relevance"), int) for p in posts)

    def sort_key(p: dict):
        rel = p.get("relevance")
        return (-(rel if isinstance(rel, int) else -1), 0 if p.get("is_new") else 1)

    posts_sorted = sorted(posts, key=sort_key)
    if any_scored:
        top = [p for p in posts_sorted if isinstance(p.get("relevance"), int) and p["relevance"] >= min_rel]
        rest = [p for p in posts_sorted if p not in top]
    else:
        top, rest = posts_sorted, []

    keywords = sorted({k for p in posts for k in (p.get("keywords") or [])})
    n_new = sum(1 for p in posts if p.get("is_new"))

    # ── page pieces ──────────────────────────────────────────────────
    warn_html = ""
    if warnings:
        items = "".join(f"<li>{_esc(w)}</li>" for w in warnings)
        warn_html = f'<section class="notice warn"><ul>{items}</ul></section>'

    if not configured:
        body_main = _setup_section()
    elif not posts:
        days = (cfg.get("search") or {}).get("days_back", 3)
        body_main = f"""<section class="notice empty">
          <h2>No fresh posts today</h2>
          <p>Nothing matching your keywords was indexed in the last {days} day(s).
          Try broader keywords in <code>config.yml</code>, or simply check back tomorrow.</p>
        </section>"""
    else:
        chip_buttons = "".join(
            f'<button class="fchip" data-filter="kw:{_esc(k)}" type="button">{_esc(k)}</button>' for k in keywords
        )
        filters = f"""<nav class="filters">
          <button class="fchip active" data-filter="all" type="button">All</button>
          <button class="fchip" data-filter="new" type="button">New today</button>
          {chip_buttons}
        </nav>"""
        cards_top = "".join(_card(p) for p in top)
        rest_html = ""
        if rest:
            rest_html = f"""<details class="lowrel">
              <summary>Lower relevance ({len(rest)})</summary>
              {"".join(_card(p) for p in rest)}
            </details>"""
        body_main = filters + f'<section class="cards">{cards_top}</section>' + rest_html

    stats = f"""<div class="stats">
      <div class="stat"><span class="stat-n">{len(posts)}</span><span class="stat-l">posts found</span></div>
      <div class="stat"><span class="stat-n">{n_new}</span><span class="stat-l">new today</span></div>
      <div class="stat"><span class="stat-n">{len(top) if any_scored else "–"}</span><span class="stat-l">worth a look</span></div>
    </div>"""

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
.wrap {{ max-width: 780px; margin: 0 auto; padding: 24px 16px 64px; }}
header h1 {{ font-size: 22px; margin: 0 0 2px; letter-spacing: -.01em; }}
.sub {{ color: var(--ink-2); font-size: 13px; margin: 0 0 18px; }}
.stats {{ display: flex; gap: 10px; margin: 0 0 18px; flex-wrap: wrap; }}
.stat {{ background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  padding: 8px 14px; display: flex; flex-direction: column; min-width: 96px; box-shadow: var(--shadow); }}
.stat-n {{ font-size: 20px; font-weight: 650; }}
.stat-l {{ font-size: 12px; color: var(--ink-2); }}
.filters {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 0 0 16px; }}
.fchip {{ background: var(--surface); color: var(--ink-2); border: 1px solid var(--line);
  border-radius: 999px; padding: 5px 12px; font-size: 13px; cursor: pointer; }}
.fchip.active {{ background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }}
.cards {{ display: flex; flex-direction: column; gap: 14px; }}
.card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
  padding: 14px 16px; box-shadow: var(--shadow); }}
.card.hidden {{ display: none; }}
.card-head {{ display: flex; gap: 12px; align-items: flex-start; }}
.card-id {{ flex: 1; min-width: 0; }}
.score {{ flex: none; width: 34px; height: 34px; border-radius: 9px; display: grid; place-items: center;
  font-weight: 700; font-size: 15px; }}
.s-high {{ background: var(--good-bg); color: var(--good-ink); }}
.s-mid  {{ background: var(--mid-bg);  color: var(--mid-ink); }}
.s-low, .s-none {{ background: var(--low-bg); color: var(--low-ink); }}
.author {{ font-weight: 620; font-size: 14px; }}
.kind {{ color: var(--ink-3); font-weight: 400; }}
.title {{ color: var(--ink-2); font-size: 13px; overflow: hidden; text-overflow: ellipsis;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
.open {{ flex: none; background: var(--accent); color: var(--accent-ink); text-decoration: none;
  border-radius: 8px; padding: 6px 12px; font-size: 13px; font-weight: 600; white-space: nowrap; }}
.snippet {{ margin: 10px 0 6px; color: var(--ink); font-size: 14px; }}
.reason {{ margin: 0 0 8px; color: var(--ink-2); font-size: 13px; font-style: italic; }}
.chips {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 4px; }}
.chip {{ font-size: 11.5px; color: var(--ink-2); border: 1px solid var(--line);
  border-radius: 999px; padding: 2px 9px; }}
.chip-new {{ background: var(--new-bg); color: var(--new-ink); border-color: var(--new-bg); font-weight: 700; }}
.drafts {{ margin-top: 8px; border-top: 1px dashed var(--line); padding-top: 8px; }}
.drafts summary {{ cursor: pointer; color: var(--accent); font-size: 13.5px; font-weight: 600; }}
.draft {{ background: var(--bg); border: 1px solid var(--line); border-radius: 10px;
  padding: 10px 12px; margin-top: 10px; }}
.draft-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }}
.draft-style {{ font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--ink-3); font-weight: 700; }}
.copy {{ background: transparent; border: 1px solid var(--line); color: var(--ink-2);
  border-radius: 7px; padding: 3px 10px; font-size: 12px; cursor: pointer; }}
.copy.done {{ color: var(--good-ink); border-color: var(--good-ink); }}
.draft-text {{ margin: 0; font-size: 14px; white-space: pre-wrap; }}
.lowrel {{ margin-top: 22px; }}
.lowrel summary {{ cursor: pointer; color: var(--ink-2); font-size: 14px; margin-bottom: 12px; }}
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
    <p class="sub">Fresh posts worth commenting on · updated {_esc(generated)}</p>
  </header>
  {stats}
  {warn_html}
  {body_main}
  <footer>Click a post's <strong>Open ↗</strong> to comment on LinkedIn (you must be logged in there).<br>
  Topics and voice live in <a href="https://github.com/{_esc(os.environ.get("GITHUB_REPOSITORY", "milosevic16/linkedin-searches"))}/blob/main/config.yml">config.yml</a>.</footer>
</div>
<script>
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
    const f = chip.dataset.filter;
    document.querySelectorAll(".card").forEach((card) => {{
      let show = true;
      if (f === "new") show = card.dataset.new === "1";
      else if (f.startsWith("kw:")) show = card.dataset.kws.split("|").includes(f.slice(3));
      card.classList.toggle("hidden", !show);
    }});
  }}
}});
</script>
</body>
</html>"""

    (site / "index.html").write_text(page, encoding="utf-8")

    # Persist state for the next run: everything ever shown, capped.
    merged_seen = prev_seen + [p["url"] for p in posts if p["url"] not in seen_set]
    if len(merged_seen) > SEEN_CAP:
        merged_seen = merged_seen[-SEEN_CAP:]
    (site / "data.json").write_text(
        json.dumps(
            {
                "generated_at": now.isoformat(),
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
