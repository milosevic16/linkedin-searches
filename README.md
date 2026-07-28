# LinkedIn Comment Radar

A dashboard that puts **LinkedIn posts worth commenting on** in front of you,
with Claude-written comment ideas ready to adapt — so building visibility takes
minutes instead of an hour of scrolling.

**It refreshes when you ask it to, not on a schedule.** There is a **Refresh**
button at the top of the dashboard and nothing runs until you press it. A refresh
costs a couple of cents.

The page is organised **by topic**. Each topic gets one section containing both
of the things you need, because no single source does both jobs:

Each topic gives you a **link into LinkedIn's own search**, pre-filtered to the
last 24 hours and sorted newest-first, plus **3 reusable comment angles** to bring
to whatever you find there.

Underneath each topic sit the **posts found in the last 24 hours**, each with
three comment drafts written for that specific post.

**Where the posts come from.** LinkedIn blocks crawlers, so web search is useless
here — we measured it, and it returned nothing newer than **112 days old**. Since
LinkedIn engagement is over within about 48 hours, those were worthless. Posts now
come from LinkedIn's own post search via [Apify](https://apify.com) (~$2 per 1,000
posts; the free plan's $5/month covers roughly 10 refreshes). It needs no LinkedIn
cookies, so your own account is never at risk.

**Filters run in code, before any AI sees a post:** the publication date is decoded
from the post's own URL and anything over 24 hours old is dropped, then anything
matching no keyword is dropped. Only what survives costs model tokens. This matters
— an earlier version left recency to the model, which cannot see a date, and put a
post from 2023 on the dashboard with a relevance of 7.

Timing matters: LinkedIn engagement decays within roughly 48 hours, so a comment
on a months-old post earns very little. Work the live search links daily; treat
the listed posts as background research.

There is nothing to install. Commenting stays manual and human — you comment on
LinkedIn logged in as yourself.

---

## For the two of you (daily use)

1. **Bookmark the dashboard:** `https://milosevic16.github.io/linkedin-searches/`
2. **Work one topic at a time.** Expand *"general angles"* for talking points,
   then click **Past 24 hours ↗** to open LinkedIn's own search for that topic,
   newest first, and comment on what's worth it.
3. **Always adapt an angle before posting** — two accounts pasting identical
   comments is the fastest way to look like bots.
5. Want it refreshed right now? Actions tab → **Build dashboard** → *Run
   workflow* → pick **gather** (needs a GitHub account with access to this repo).

> You must be **logged in to LinkedIn** in the same browser for the top links to
> work. If a link shows a login wall, log in and click it again.

### Changing what it searches for

Everything lives in [`config.yml`](config.yml) — keywords, who you are, comment
tone, freshness window. Edit it directly on GitHub (pencil icon → commit); the
dashboard rebuilds itself in ~2 minutes. No coding involved.

**Editing config is cheap.** The gathered posts are stored in `data/latest.json`,
so a rebuild only redoes the stage whose inputs actually changed:

| You edit | What re-runs | Cost |
|---|---|---|
| wording, layout, thresholds | the page only | **free** |
| `voice`, `profile` | rewrites the comment angles | a couple of cents |
| `keywords` | new angles for the new topics | a couple of cents |

So you can iterate on comment tone as often as you like without paying to search
LinkedIn again each time. Only pressing **Refresh posts** starts a search.

If you change a keyword, the page keeps showing the previous search's posts and
tells you to press Refresh — an edit never spends credit on its own.

Each build prints a cost table to the Actions log — tokens per stage and an
estimate in USD and EUR — and the page footer shows what that run cost.

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
.github/workflows/dashboard.yml   manual button (+ config edits) → build → Pages
                                  no schedule: searching only happens on request
config.yml                        keywords, profile, voice, thresholds, models
scripts/build.py                  orchestrates; picks gather / redraft / render
scripts/store.py                  data/latest.json + the fingerprint that decides
                                  which stage actually needs to re-run
scripts/launcher.py               LinkedIn search deep-links (datePosted +
                                  sortBy=date_posted) + general comment angles
scripts/search_claude.py          Claude web_search tool (allowed_domains:
                                  linkedin.com) → post URLs + snippets. Runs on
                                  search_model (Sonnet) — it is the mechanical step
scripts/enrich.py                 Claude (claude-opus-5, structured outputs):
                                  relevance 0–10 + reason + 3 per-post drafts
scripts/usage.py                  token accounting → cost table in the build log
scripts/render.py                 static HTML dashboard + data.json (remembers
                                  previously-shown URLs → NEW badges)
```

- **Gathering and rendering are separate.** Searching is the only expensive part,
  so its output is stored in `data/latest.json` and committed back by the
  workflow. Rebuilding the page from that data costs nothing. See the table under
  *Changing what it searches for*.
- **Cost control, in descending order of impact:** the pause-turn resume loop is
  prompt-cached (without it every resume re-billed every search result already
  gathered); the search step runs on Sonnet at medium effort rather than Opus at
  the default high effort with thinking on; the scoring system prompt is cached
  across batches. Scoring and drafting stay on Opus — that is the output you read.

- **No hallucinated links:** URLs come from the search tool's own result blocks,
  not from Claude's prose. Anything the model mentions that the search did not
  actually return is discarded before it can reach the dashboard.
- **How many posts you get:** one web search returns roughly 10 results and that
  count is not adjustable — the API bills per *search*, not per result. So the
  only lever on volume is `searches_per_keyword` in `config.yml` (each phrased
  differently to widen the net). The "Notable" section is often small, or empty
  on niche keywords; that is the index being thin, not a bug. The live search
  links never run dry, because LinkedIn answers those queries itself.
- **Only vouched-for posts are shown.** The model applies the recency and quality
  rules and returns a filtered list; the raw result set (full of 2021–2023 posts)
  is used solely as a URL whitelist. If it finds nothing suitable it returns an
  empty list and the section says so, rather than padding with stale posts.
- **Dynamic filtering is on.** The search tool prunes results inside code
  execution before they reach the context window. It was switched off at first,
  to be sure no candidate post was dropped before the model could judge it —
  but measuring a real run showed that choice was pushing roughly 50,000 tokens
  of raw results per keyword and accounted for 88% of the bill. The tradeoff
  now runs the other way: a marginal post may be filtered out unseen.
- **The URL whitelist survives either setting.** Harvesting walks the response
  tree for `web_search_result` blocks rather than assuming they sit at the top
  level, because with filtering on they may not. If a keyword's results are
  described but none match a harvested URL, the run warns instead of silently
  rendering an empty topic.
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

ANTHROPIC_API_KEY=… python scripts/build.py --gather    # full run, costs money
ANTHROPIC_API_KEY=… python scripts/build.py --redraft   # re-score stored posts
ANTHROPIC_API_KEY=… python scripts/build.py --render    # rebuild page, free
ANTHROPIC_API_KEY=… python scripts/build.py --auto      # cheapest sufficient mode
```

`--render` and `--redraft` need a `data/latest.json` to work from; without one
they fall back to gathering and say so. Every run prints a cost table at the end.
