# One-click refresh — setup

This makes each dashboard's **Refresh** button work with a shared password, so
anyone with the link and the password can refresh the posts. No GitHub account,
no signing in.

It takes about ten minutes, once. Everything is on free tiers.

One worker serves every company. The page sends which company it is alongside
the password, and only that company gets refreshed. The password is the same for
all of them.

## Why a worker is needed at all

The dashboard is static HTML on a public site. Triggering a refresh requires a
GitHub token with write access, and **anything in the page is public** — a
visitor can read the source. A password checked in the page's JavaScript would
not help, because the token would be sitting right next to it.

So the token lives in a tiny server-side function instead. The browser sends
only the password; the function checks it and calls GitHub. Nothing secret is
ever served to a visitor.

## 1. Create a GitHub token

Go to **[Fine-grained tokens](https://github.com/settings/personal-access-tokens/new)**.

- **Repository access** → *Only select repositories* → `linkedin-searches`
- **Permissions** → *Repository permissions* → **Actions: Read and write**
- Nothing else. This token must not be able to do anything but run this workflow.
- **Expiration**: 90 days is sensible. Note the date — the button stops working
  when it expires, and the page will say GitHub refused the request.

Copy the token (`github_pat_…`). GitHub shows it once.

## 2. Deploy the worker

Sign up at **[cloudflare.com](https://dash.cloudflare.com/sign-up)** (free).

Then: **Compute (Workers)** → **Create** → **Start from Hello World** → name it
`linkedin-refresh` → **Deploy**. Click **Edit code**, delete what's there, paste
the contents of [`refresh-worker.js`](refresh-worker.js), and **Deploy** again.

## 3. Add the settings

On the worker: **Settings** → **Variables and Secrets**.

Add as **Secret** (encrypted, not readable afterwards):

| Name | Value |
|---|---|
| `GITHUB_TOKEN` | the `github_pat_…` from step 1 |
| `REFRESH_PASSWORD` | your chosen password |

Add as **Text**:

| Name | Value |
|---|---|
| `GITHUB_REPO` | `milosevic16/linkedin-searches` |
| `ALLOWED_ORIGIN` | `https://milosevic16.github.io` |

`ALLOWED_ORIGIN` stops other websites from calling your worker from a
visitor's browser. Set it exactly, with no trailing slash.

**Deploy** after adding them — variables only take effect on a new deployment.

## 4. Point the dashboard at it

Copy the worker's URL (`https://linkedin-refresh.<your-subdomain>.workers.dev`).

Edit [`config.yml`](../config.yml) and set:

```yaml
refresh_endpoint: "https://linkedin-refresh.your-subdomain.workers.dev"
```

Commit. Every dashboard rebuilds in a couple of minutes — free, no searching —
and each Refresh button becomes a password box. One endpoint serves them all.

If you add a company later, add its slug to `COMPANIES` at the top of
[`refresh-worker.js`](refresh-worker.js) and redeploy, or the worker will refuse
its refreshes with *Unknown company*.

## What the worker will and will not do

- **Only ever starts this one workflow.** It cannot read the repository, change
  code, or run anything else, because the token has no other permission.
- **Bounds what it can cost.** Refuses within 10 minutes of *that company's*
  last refresh, refuses while any refresh is running, and refuses past **10 a
  day per company** or **30 in 30 days across all of them**. Each refresh costs
  money at two vendors and the password is shared by design, so assume it gets
  around — the cooldown alone would still allow 144 a day. To change the limits,
  edit the constants at the top of the worker and redeploy; the test suite reads
  them from there.
  Sizing: a refresh costs about **$0.62** measured — $0.44 Anthropic, $0.18
  Apify — so 30 a month is roughly a $19 ceiling. Apify's free $5/month covers
  about 27 of the Apify half; the Anthropic side has no free tier, which is why
  the monthly cap is on the total rather than one each.
- **Knows which company spent what.** The cooldown and the daily cap are per
  company, so refreshing one dashboard never locks the other's button for ten
  minutes. It works this out from the run title the workflow sets (`run-name:`
  in `dashboard.yml`). A run whose title it cannot read is charged to **every**
  company — so if that ever breaks, the caps get stricter, never laxer. That
  direction is deliberate: attribution failing open would silently uncap
  spending, and nothing downstream would notice.
- **Fails closed.** If GitHub cannot be read, the refresh is refused rather than
  allowed. Otherwise a transient GitHub error would silently switch off every
  limit above, which is the wrong direction to fail in when the failure spends
  money.
- **Answers every request at the same speed**, and compares the password in
  constant time, so neither timing nor response speed hints at a partial match.
- **Never returns the token or the password**, in any response or error.

## If it stops working

The page shows the reason under the button.

| Message | Cause |
|---|---|
| *Wrong password* | Mistyped, or `REFRESH_PASSWORD` changed |
| *Unknown company* | The page's slug is not in `COMPANIES` in the worker — add it and redeploy |
| *A refresh is already running* | Wait for it — one runs at a time across all companies |
| *&lt;company&gt; was refreshed N min ago* | That company's 10-minute cooldown. The other company's button still works |
| *N refreshes for &lt;company&gt; in the last 24 hours* | Its daily cap — raise the constants in the worker if the budget allows |
| *N refreshes across all companies in the last 30 days* | The shared monthly cap. It is shared because the Anthropic and Apify bills are |
| *Could not check recent refreshes* | GitHub was unreadable, so it refused rather than risk an uncapped run. Try again shortly |
| *GitHub refused the request (401/403)* | Token expired or its permissions were reduced — redo step 1 |
| *Could not reach the refresh service* | Worker not deployed, or `refresh_endpoint` is wrong |

To change the password, update the `REFRESH_PASSWORD` secret and redeploy. No
change to the dashboard is needed — it never knew the password.

To turn one-click refresh off entirely, set `refresh_endpoint: ""`. The button
reverts to linking at the GitHub Actions page, which needs an account.
