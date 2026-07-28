# LinkedIn Comment Radar

A self-refreshing dashboard that puts **LinkedIn posts worth commenting on** in
front of you every morning, with Claude-written comment ideas ready to adapt — so
building visibility takes minutes instead of an hour of scrolling.

The dashboard has **two sections**, because no single source does both jobs:

| Section | What it is | Why |
|---|---|---|
| **Fresh posts — comment today** | One-click links into LinkedIn's *own* search per keyword, pre-filtered to the last 24 hours and sorted newest-first, plus 3 reusable comment angles per topic | LinkedIn blocks crawlers, so **its own search is the only place today's posts exist**. You land on genuinely fresh posts and comment while they're live. |
| **Notable in your niche** | AI-scored posts from roughly the last 90 days, each with a relevance score and 2–3 ready comment drafts | Search engines index only a thin, delayed slice of LinkedIn. Good for finding people and threads worth knowing — *not* today's commenting queue. |

**Why the split:** we measured it. Live web searches restricted to `linkedin.com`
returned results whose newest entries were ~2 months old, with the bulk from
2021–2023. Nothing from the past week. So the top section hands you into LinkedIn
directly for freshness, and the bottom section uses AI where it genuinely helps.

Timing matters: LinkedIn engagement decays within roughly 48 hours, so a comment
on a months-old post earns very little. Work the top section daily; treat the
bottom as background research.

There is nothing to install. Commenting stays manual and human — you comment on
LinkedIn logged in as yourself.

---

## For the two of you (daily use)

1. **Bookmark the dashboard:** `https://milosevic16.github.io/linkedin-searches/`
2. **Start at the top.** For each topic, expand *"comment angles"* to load your
   talking points, then click **Past 24 hours ↗**. LinkedIn opens showing today's
   posts on that topic, newest first. Comment on the ones worth it.
3. **Then skim "Notable in your niche"** for people and discussions to follow.
   **NEW** marks anything not shown on a previous run.
4. **Always adapt a draft before posting** — two accounts pasting identical
   comments is the fastest way to look like bots.
5. Want it refreshed right now? Actions tab → **Build dashboard** → *Run
   workflow* (needs a GitHub account with access to this repo).

> You must be **logged in to LinkedIn** in the same browser for the top links to
> work. If a link shows a login wall, log in and click it again.

### Changing what it searches for

Everything lives in [`config.yml`](config.yml) — keywords, who you are, comment
tone, freshness window. Edit it directly on GitHub (pencil icon → commit); the
dashboard rebuilds itself in ~2 minutes. No coding involved.

---

## One-time setup (owner)

The tool needs **one secret**: an Anthropic API key. It does the searching, the
scoring, and the comment drafting.

### 1. Get an Anthropic API key

1. Go to <https://console.anthropic.com/> and sign in (or create an account).
2. Add a payment method under **Billing** — usage is pay-as-you-go.
3. Go to **API keys → Create key**, name it something like `linkedin-radar`,
   and copy the value. It starts with `sk-ant-`. **Copy it now — the console
   will not show it again.**

**Expected cost: roughly $2–4 per month.** Web searches are billed at $10 per
1,000, and this tool runs about 10 per weekday; the scoring and drafting tokens
add ~$1. You can cap spending in the console under **Billing → Limits**.

### 2. Wire it up on GitHub

1. Repo **Settings → Secrets and variables → Actions → New repository secret**.
   Name it exactly `ANTHROPIC_API_KEY`, paste the key, save.
2. Repo **Settings → Pages** → under *Build and deployment* set **Source:
   GitHub Actions** (the first workflow run usually enables this by itself).
3. Trigger the first run: Actions tab → **Build dashboard** → *Run workflow*.
   It takes 1–3 minutes.

If the secret is missing, the dashboard still deploys — it shows a friendly
"setup needed" page instead of failing silently.

> **Why not Google?** Google closed its free Custom Search JSON API to new
> customers in January 2026 and shuts it down entirely on 1 January 2027, so it
> is not an option for a new project. Claude's built-in web search replaces it
> and needs no second account.

> **Note on privacy:** the repo and the dashboard URL are public. Anyone with
> the link can see your keywords and the drafted comments. Fine for most
> growth workflows, but don't put anything confidential in `config.yml`.

> **Note on scope:** this tool deliberately does **not** auto-post comments.
> Automated commenting violates LinkedIn's terms and gets accounts restricted;
> reading and clicking through search results does not.

---

## How the pieces fit

```
.github/workflows/dashboard.yml   schedule + manual button → build → deploy to Pages
config.yml                        keywords, profile, voice, thresholds
scripts/launcher.py               LinkedIn search deep-links (datePosted +
                                  sortBy=date_posted) + reusable comment angles
scripts/search_claude.py          Claude web_search tool (allowed_domains:
                                  linkedin.com) → notable post URLs + snippets
scripts/enrich.py                 Claude (claude-opus-5, structured outputs):
                                  relevance 0–10 + reason + 2–3 comment drafts
scripts/render.py                 static HTML dashboard + data.json (remembers
                                  previously-shown URLs → NEW badges)
```

- **No hallucinated links:** URLs come from the search tool's own result blocks,
  not from Claude's prose. Anything the model mentions that the search did not
  actually return is discarded before it can reach the dashboard.
- **How many posts you get:** one web search returns roughly 10 results and that
  count is not adjustable — the API bills per *search*, not per result. So the
  only lever on volume is `searches_per_keyword` in `config.yml` (each phrased
  differently to widen the net). The "Notable" section is often small, or empty
  on niche keywords; that is the index being thin, not a bug. The top section
  never runs dry, because LinkedIn answers those queries itself.
- **Only vouched-for posts are shown.** The model applies the recency and quality
  rules and returns a filtered list; the raw result set (full of 2021–2023 posts)
  is used solely as a URL whitelist. If it finds nothing suitable it returns an
  empty list and the section says so, rather than padding with stale posts.
- **Dynamic filtering is deliberately off** (`allowed_callers: ["direct"]`). By
  default this tool version filters results inside code execution before Claude
  sees them, which would silently drop candidate posts.
- **Dedupe:** the previous deploy's `data.json` is fetched over HTTPS, so posts
  already shown on an earlier run lose their NEW badge.
- **Two windows, two settings:** `fresh_window` controls the LinkedIn links at the
  top (`past-24h` / `past-week` / `past-month`); `notable_days` controls how far
  back the scored section reaches. Setting `notable_days` under ~30 will usually
  return nothing, because the index is that sparse.
- **Failure behavior:** rate limits, API errors, and skipped steps surface as a
  yellow warning box on the dashboard itself rather than an empty page.

## Local development

```bash
pip install -r requirements.txt
python scripts/build.py --sample        # offline preview, no API key needed
ANTHROPIC_API_KEY=… python scripts/build.py
```
