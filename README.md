# LinkedIn Comment Radar

A dashboard that puts **LinkedIn posts worth commenting on** in front of you,
with Claude-written comment ideas ready to adapt — so building visibility takes
minutes instead of an hour of scrolling.

**It serves two companies**, each with its own dashboard, keywords, voice and
budget:

| | Dashboard | Settings |
|---|---|---|
| **Bloctopus Intelligence** | `https://milosevic16.github.io/linkedin-searches/` | [`companies/bloctopus.yml`](companies/bloctopus.yml) |
| **Lemur Legal** | `https://milosevic16.github.io/linkedin-searches/lemur/` | [`companies/lemur.yml`](companies/lemur.yml) |

The tabs at the top of either page switch between them. Everything below applies
to both.

**It refreshes when you ask it to, not on a schedule.** There is a **Refresh**
button at the top of each dashboard and nothing runs until you press it. Refresh
is per company: refreshing one does not search for, spend on, or touch the other.

The page is organised **by topic**. Each topic gives you a **link into LinkedIn's
own search**, pre-filtered to the last 24 hours and sorted newest-first, and
underneath it the **posts found in the last 48 hours**, each with three comment
drafts written for that specific post.

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
   — the tabs at the top switch to Lemur Legal's and back.
2. **Work one topic at a time.** Read the posts in its section and expand a
   post's drafts. Or click **Past 24 hours ↗** to open LinkedIn's own search for
   that topic, newest first, and comment on what's worth it there.
3. **Always adapt a draft before posting** — two accounts pasting identical
   comments is the fastest way to look like bots.
4. **Want fresh posts?** Type the password into the box at the top and press
   **Refresh**. It searches for the company whose page you are on. Takes about
   five minutes; reload the page when it's done.

> You must be **logged in to LinkedIn** in the same browser for the top links to
> work. If a link shows a login wall, log in and click it again.

### Changing what it searches for

Each company's settings live in its own file — [`companies/bloctopus.yml`](companies/bloctopus.yml)
and [`companies/lemur.yml`](companies/lemur.yml) — covering keywords, who you
are, comment tone and freshness window. Edit one directly on GitHub (pencil icon
→ commit); the dashboards rebuild themselves in ~2 minutes. No coding involved.

The files repeat each other in places (the same model names, the same
thresholds) and that is deliberate rather than sloppy. Those settings decide
whether a build has to pay to redo its work. Shared in one file, nudging
`min_relevance` by a digit would re-draft **every** company at once and bill for
all of them from a single commit. Kept separate, editing Lemur can never charge
Bloctopus.

[`config.yml`](config.yml) holds only the roster and the refresh endpoint —
nothing that costs money. The build refuses to start if a paid setting appears
there, so that property cannot quietly rot.

**Editing settings never spends money on its own.** Each company's gathered
posts are stored in `data/<company>.json`, and a rebuild only redoes the stage
whose inputs actually changed:

| You edit | What happens | Cost |
|---|---|---|
| wording, layout | that company's page rebuilds | **free** |
| `voice`, `profile`, `model`, `min_relevance` | page says the drafts are from the old settings, and to press **Refresh** | **free** |
| `keywords`, search settings | page keeps the previous posts and asks you to press **Refresh** | **free** |
| pressing **Refresh** | searches, scores and re-drafts, for that company only | **~$0.62** |

Nothing is billed until someone presses Refresh. That is the whole design: a
commit can never start a search or a re-write, only the button can.

Each build prints a cost table to the Actions log — tokens per stage and an
estimate in USD and EUR — and the page footer shows what that run cost.

### Adding another company

1. Copy an existing file to `companies/<slug>.yml` and edit the name, keywords,
   profile and voice.
2. Add the slug to `companies:` in [`config.yml`](config.yml).
3. Add it to the `company:` choices in
   [`.github/workflows/dashboard.yml`](.github/workflows/dashboard.yml) and to
   `COMPANIES` in [`worker/refresh-worker.js`](worker/refresh-worker.js), then
   redeploy the worker.

Its page appears at `/<slug>/`. While the profile still contains
`FILL-THIS-IN`, pressing Refresh renders the page but refuses to spend — a
gather against template copy costs full price and produces drafts written for a
company that does not exist.

---

## One-time setup (owner)

The tool needs **two secrets**: an Anthropic API key, which scores the posts and
drafts the comments, and an Apify token, which finds the posts. Both are shared
across the companies.

### 1. Get an Anthropic API key

1. Go to <https://console.anthropic.com/> and sign in (or create an account).
2. Add a payment method under **Billing** — usage is pay-as-you-go.
3. Go to **API keys → Create key**, name it something like `linkedin-radar`,
   and copy the value. It starts with `sk-ant-`. **Copy it now — the console
   will not show it again.**

**Expected cost: about $0.62 per refresh**, measured, not estimated — $0.44 of
Anthropic tokens and $0.18 of Apify. Nothing runs on a schedule, so the monthly
bill is however many times you press the button. The worker refuses past 30
refreshes in 30 days across both companies, which puts the ceiling around $19 a
month. You can also cap spending in the Anthropic console under
**Billing → Limits**.

### 2. Wire it up on GitHub

1. Repo **Settings → Secrets and variables → Actions → New repository secret**.
   Name it exactly `ANTHROPIC_API_KEY`, paste the key, save. Repeat for
   `APIFY_TOKEN` (from <https://console.apify.com/> → Settings → API & Integrations).
2. Repo **Settings → Pages** → under *Build and deployment* set **Source:
   GitHub Actions** (the first workflow run usually enables this by itself).
3. Trigger the first run: Actions tab → **Build dashboard** → *Run workflow*.
   It takes 1–3 minutes.

If the secret is missing, the dashboard still deploys — it shows a friendly
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
.github/workflows/dashboard.yml   manual button (+ config edits) → build → Pages
                                  no schedule: searching only happens on request
                                  run-name carries the company; the worker reads
                                  it back to know who spent what
config.yml                        the roster, and the refresh endpoint. Nothing
                                  that costs money may live here
companies/<slug>.yml              one complete config per company: keywords,
                                  profile, voice, thresholds, models
scripts/companies.py              the roster and every per-company path
scripts/build.py                  orchestrates; picks gather / redraft / render.
                                  Paid work is scoped to one company; rendering
                                  always covers all of them, because a Pages
                                  deploy replaces the whole site
scripts/store.py                  data/<slug>.json, data/<slug>-seen.json, and
                                  the fingerprint that decides which stage
                                  actually needs to re-run
scripts/launcher.py               LinkedIn search deep-links (datePosted +
                                  sortBy=date_posted). Free — they are just URLs
scripts/search_apify.py           Apify LinkedIn post search → posts, then the
                                  hardcoded date and keyword filters
scripts/postdate.py               decodes the post date from the URL's own ID
scripts/enrich.py                 Claude, structured outputs: score every post
                                  (score_model) → draft 3 comments for the ones
                                  that clear min_relevance (model)
scripts/usage.py                  token accounting → cost table in the build log
scripts/render.py                 static HTML dashboard + data.json (remembers
                                  previously-shown URLs → NEW badges)
```

- **Gathering and rendering are separate.** Searching and drafting are the only
  expensive parts, so their output is stored in `data/<company>.json` and
  committed back by the workflow. Rebuilding a page from that data costs nothing.
- **Nothing but the button spends.** A push runs with `--no-gather`, which blocks
  both searching *and* re-drafting. That second half matters more than it looks:
  a re-draft is about $0.42, it is what a `min_relevance` or `model` edit
  triggers, and none of the spend caps in the worker can see a push.
- **Paid work is scoped to one company; rendering is not.** A GitHub Pages deploy
  replaces the entire site, so a run that rendered only the company it gathered
  for would 404 the other one. Rendering is free, so every run rebuilds every
  page and the deploy is always complete.
- **Filters run in code, before any AI sees a post:** the publication date is
  decoded from the post's own URL and anything past `notable_max_age_hours` is
  dropped, then anything matching no keyword is dropped. Only what survives costs
  tokens.
- **Two ceilings live in code, not config.** `max_enriched` is the only real
  brake on the Anthropic side of the bill — around 70% of it — so `enrich.py`
  caps it regardless of what a config file asks for. `max_usd_per_run` guards the
  Apify side, and refuses the run rather than trimming it, because Apify charges
  on posts returned.
- **NEW badges** come from `data/<company>-seen.json`, in git. It used to live
  only on the published site and be fetched back over HTTPS, which meant a
  transient error read as "nothing seen yet" and wiped it — and once there were
  two companies, publishing one page would have deleted the other's copy.
- **Failure behavior:** rate limits, API errors, and skipped steps surface as a
  yellow warning box on the dashboard itself rather than an empty page. A failed
  search keeps the previous posts rather than replacing them with nothing.

## Local development

```bash
pip install -r requirements.txt
python scripts/build.py --sample                     # offline preview, no key
node worker/test.mjs                                 # spend-cap tests (in worker/)

ANTHROPIC_API_KEY=… python scripts/build.py --company lemur --gather   # costs money
ANTHROPIC_API_KEY=… python scripts/build.py --company lemur --redraft  # costs money
ANTHROPIC_API_KEY=… python scripts/build.py --render                   # free
ANTHROPIC_API_KEY=… python scripts/build.py --auto --no-gather         # what a push runs
```

`--gather` and `--redraft` spend money, so they refuse to run without
`--company`. `--render` and `--redraft` need a `data/<company>.json` to work
from; without one they fall back to gathering and say so. Every run prints a cost
table at the end.
