# LinkedIn Comment Radar

A self-refreshing dashboard that finds **fresh public LinkedIn posts worth
commenting on**, scores them for relevance with Claude, and drafts 2–3 comment
ideas per post — so building LinkedIn visibility takes minutes, not an hour of
scrolling.

**How it works:** every weekday morning a GitHub Action asks Claude to search the
web for public LinkedIn posts matching your keywords → Claude scores each post and
drafts comments in your voice → the results are published as a simple web page.

There is nothing to install. Commenting itself stays manual and human: you open
the post on LinkedIn (logged in as yourself) and paste/adapt a draft.

---

## For the two of you (daily use)

1. **Bookmark the dashboard:** `https://milosevic16.github.io/linkedin-searches/`
2. Open it with your morning coffee. Posts are sorted by relevance; **NEW** marks
   posts you haven't seen on a previous day.
3. Click a post's **Open ↗**, read it, then expand *"comment ideas"*, hit
   **Copy**, and adapt the draft to your own words before posting. (Always adapt —
   identical comments from two accounts look like bots.)
4. Want it refreshed right now? Actions tab → **Build dashboard** → *Run
   workflow* (needs a GitHub account with access to this repo).

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

**Expected cost: roughly $3–5 per month.** Web searches are billed at $10 per
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
scripts/search_claude.py          Claude web_search tool (allowed_domains:
                                  linkedin.com) → recent post URLs + snippets
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
  differently to widen the net). Expect on the order of 10–25 usable posts per
  run from 3 keywords × 3 searches, after duplicates and non-post URLs are
  dropped. Raise the number, or add keywords, if you want more.
- **Dynamic filtering is deliberately off** (`allowed_callers: ["direct"]`). By
  default this tool version filters results inside code execution before Claude
  sees them, which would silently drop candidate posts.
- **Freshness / dedupe:** searches target the last `days_back` days; the previous
  deploy's `data.json` is fetched so already-shown posts lose their NEW badge.
- **Coverage caveat:** search engines index many but not all public LinkedIn
  posts, with a delay of hours to days. Treat the dashboard as a strong daily
  shortlist, not an exhaustive feed.
- **Failure behavior:** rate limits, API errors, and skipped steps surface as a
  yellow warning box on the dashboard itself rather than an empty page.

## Local development

```bash
pip install -r requirements.txt
python scripts/build.py --sample        # offline preview, no API key needed
ANTHROPIC_API_KEY=… python scripts/build.py
```
