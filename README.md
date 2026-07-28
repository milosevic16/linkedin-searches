# LinkedIn Comment Radar

A self-refreshing dashboard that finds **fresh public LinkedIn posts worth
commenting on**, scores them for relevance with Claude, and drafts 2–3 comment
ideas per post — so building LinkedIn visibility takes minutes, not an hour of
scrolling.

**How it works:** every weekday morning a GitHub Action searches Google's index
of public LinkedIn posts for your keywords → Claude scores each post and drafts
comments in your voice → the results are published as a simple web page.

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

The tool needs three secrets. Total cost at this scale: **$0 for search** (free
tier) and **a few cents per day** for Claude.

### 1. Google Programmable Search Engine (finds the posts)

1. Go to <https://programmablesearchengine.google.com/> → **Add**.
2. Under *"What to search"* choose **Search specific sites** and add:
   `linkedin.com/posts/*` and `linkedin.com/pulse/*`
3. Create it, then copy the **Search engine ID** → this is `GOOGLE_CSE_ID`.
4. Get an API key at
   <https://developers.google.com/custom-search/v1/overview> → **Get a key**
   → this is `GOOGLE_API_KEY`. Free tier: 100 searches/day (this tool uses
   ~2 per keyword per run).

### 2. Anthropic API key (scores posts + drafts comments)

Create a key at <https://console.anthropic.com/> → API keys → this is
`ANTHROPIC_API_KEY`. Usage here is tiny (short snippets in, short drafts out).

### 3. Wire it up on GitHub

1. Repo **Settings → Secrets and variables → Actions → New repository secret**,
   add all three: `GOOGLE_API_KEY`, `GOOGLE_CSE_ID`, `ANTHROPIC_API_KEY`.
2. Repo **Settings → Pages** → under *Build and deployment* set **Source:
   GitHub Actions** (the first workflow run usually enables this by itself).
3. Make sure this code is on the **`main` branch** — scheduled workflows only
   run from the default branch.
4. Trigger the first run: Actions tab → **Build dashboard** → *Run workflow*.

If a secret is missing, the dashboard still deploys — it shows a friendly
"setup needed" page instead of failing silently.

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
scripts/search_google.py          Google CSE → recent linkedin.com/posts results
scripts/enrich.py                 Claude (claude-opus-5, structured outputs):
                                  relevance 0–10 + reason + 2–3 comment drafts
scripts/render.py                 static HTML dashboard + data.json (remembers
                                  previously-shown URLs → NEW badges)
```

- **Freshness / dedupe:** searches cover the last `days_back` days; the previous
  deploy's `data.json` is fetched so already-shown posts lose their NEW badge.
- **Coverage caveat:** Google indexes many but not all public LinkedIn posts,
  with a delay of hours to days. Treat the dashboard as a strong daily shortlist,
  not an exhaustive feed.
- **Failure behavior:** quota hits, API errors, and skipped steps surface as a
  yellow warning box on the dashboard itself.

## Local development

```bash
pip install -r requirements.txt
python scripts/build.py --sample     # offline preview → site/index.html
GOOGLE_API_KEY=… GOOGLE_CSE_ID=… ANTHROPIC_API_KEY=… python scripts/build.py
```
