# One-click refresh — setup

This makes the dashboard's **Refresh** button work with a shared password, so
anyone with the link and the password can refresh the posts. No GitHub account,
no signing in.

It takes about ten minutes, once. Everything is on free tiers.

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

Commit. The dashboard rebuilds in a couple of minutes — free, no searching —
and the Refresh button becomes a password box.

## What the worker will and will not do

- **Only ever starts this one workflow.** It cannot read the repository, change
  code, or run anything else, because the token has no other permission.
- **Refuses a refresh within 10 minutes of the last one**, and refuses while one
  is already running. Each refresh costs money at two vendors, and the password
  is shared and simple by design — assume it gets around. This is what stops
  someone holding down the button and draining the month's credit.
- **Answers every request at the same speed**, and compares the password in
  constant time, so neither timing nor response speed hints at a partial match.
- **Never returns the token or the password**, in any response or error.

## If it stops working

The page shows the reason under the button.

| Message | Cause |
|---|---|
| *Wrong password* | Mistyped, or `REFRESH_PASSWORD` changed |
| *A refresh is already running* | Wait for it — reload in a few minutes |
| *Refreshed N min ago* | The 10-minute cooldown |
| *GitHub refused the request (401/403)* | Token expired or its permissions were reduced — redo step 1 |
| *Could not reach the refresh service* | Worker not deployed, or `refresh_endpoint` is wrong |

To change the password, update the `REFRESH_PASSWORD` secret and redeploy. No
change to the dashboard is needed — it never knew the password.

To turn one-click refresh off entirely, set `refresh_endpoint: ""`. The button
reverts to linking at the GitHub Actions page, which needs an account.
