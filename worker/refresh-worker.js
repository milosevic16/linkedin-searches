/**
 * Refresh proxy for the LinkedIn Comment Radar dashboard.
 *
 * The dashboard is static HTML on a public GitHub Pages site. Triggering a
 * workflow needs a GitHub token with write access, and anything shipped to the
 * browser is public — a token in the page would be readable by anyone who
 * opened dev tools, and a password checked in JavaScript would not protect it,
 * because the token is the secret, not the password.
 *
 * So the token lives here instead. The browser sends only the password; this
 * checks it and calls GitHub on the caller's behalf. Nothing secret is ever
 * served to a visitor.
 *
 * The password is deliberately simple and shared, so assume it gets around.
 * What stops that mattering is scope and rate: the token can start this one
 * workflow and nothing else, and the caps below bound what it can cost. Those
 * caps read GitHub's own run history rather than any stored state, so there is
 * nothing extra to set up and nothing to get out of sync.
 *
 * Deploy: Cloudflare Workers (free tier is far more than enough).
 * Secrets:  GITHUB_TOKEN       fine-grained PAT, Actions: read+write, this repo only
 *           REFRESH_PASSWORD   the shared password
 * Vars:     GITHUB_REPO        e.g. milosevic16/linkedin-searches
 *           ALLOWED_ORIGIN     e.g. https://milosevic16.github.io
 */

const WORKFLOW = "dashboard.yml";
const REF = "main";

// Every company with a dashboard. Must match the roster in config.yml — a
// request naming anything else is refused before it can reach GitHub.
const COMPANIES = ["bloctopus", "lemur"];

// A refresh costs money at two vendors. The cooldown paces refreshes; the
// caps bound them. Without the caps, someone with the password could trigger
// 144 a day forever against a budget of roughly ten a month.
//
// The cooldown and the daily cap are PER COMPANY: refreshing one dashboard
// should not lock the other's button for ten minutes. The monthly cap is
// SHARED, because the bills are — one Apify plan, one Anthropic key. Making
// it per-company too would look symmetrical and quietly double the ceiling.
const COOLDOWN_MINUTES = 10;
const MAX_REFRESHES_PER_DAY = 10;
const MAX_REFRESHES_PER_30_DAYS = 30;

// Deliberately slow to answer, so guessing the password is tedious. The test
// suite sets __TEST_FAST__ to skip the wait; in Cloudflare it is undefined.
const MIN_RESPONSE_MS = globalThis.__TEST_FAST__ ? 0 : 4000;

function allowedOrigin(env) {
  return (env.ALLOWED_ORIGIN || "").replace(/\/+$/, "");
}

function cors(env) {
  const h = {
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Cache-Control": "no-store",
  };
  if (env.ALLOWED_ORIGIN) h["Access-Control-Allow-Origin"] = allowedOrigin(env);
  return h;
}

function reply(body, status, env) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...cors(env) },
  });
}

/** Compare in constant time, so the response cannot reveal how much of the
 *  password was right. An empty value never matches: two empty strings compare
 *  equal, so without this an unset secret would accept an empty password. */
function sameSecret(a, b) {
  const x = new TextEncoder().encode(String(a ?? ""));
  const y = new TextEncoder().encode(String(b ?? ""));
  if (x.length === 0 || y.length === 0) return false;
  if (x.length !== y.length) return false;
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

/** Which company a past run spent on, read from the title the workflow sets
 *  (`run-name:` in dashboard.yml arrives here as display_title).
 *
 *  Returns null when the title says nothing recognisable: a run from before
 *  the title existed, or a workflow edit that broke the format. Callers charge
 *  an unattributable run to EVERY company, so a title this cannot read makes
 *  the caps stricter rather than switching them off. Attribution that fails
 *  open is the one failure mode this must not have — it guards real money, and
 *  nothing downstream would notice it had stopped counting. */
function companyOf(run) {
  const m = /company:([a-z0-9_-]+)/i.exec(String((run && run.display_title) || ""));
  const slug = m && m[1].toLowerCase();
  return slug && COMPANIES.includes(slug) ? slug : null;
}

/** The runs that count against `slug`: its own, plus every unattributable one. */
function chargedTo(runs, slug) {
  return runs.filter((r) => {
    const owner = companyOf(r);
    return owner === null || owner === slug;
  });
}

async function github(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "linkedin-radar-refresh",
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
}

/** Recent runs, newest first. Throws rather than returning empty when GitHub
 *  cannot be read: every spend control below is derived from this list, so a
 *  transient GitHub error must block the refresh, not silently unlock it. */
async function recentRuns(env, query = "") {
  const res = await github(env, `/actions/workflows/${WORKFLOW}/runs?per_page=100${query}`);
  if (!res.ok) throw new Error(`runs lookup failed (${res.status})`);
  const data = await res.json();
  return data.workflow_runs ?? [];
}

export default {
  async fetch(request, env) {
    const started = Date.now();
    const settle = async (body, status) => {
      const wait = MIN_RESPONSE_MS - (Date.now() - started);
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
      return reply(body, status, env);
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(env) });
    }
    if (request.method !== "POST") {
      return reply({ ok: false, error: "Method not allowed." }, 405, env);
    }

    if (!env.GITHUB_TOKEN || !env.REFRESH_PASSWORD || !env.GITHUB_REPO || !env.ALLOWED_ORIGIN) {
      return reply({ ok: false, error: "Refresh is not configured yet." }, 500, env);
    }

    const origin = request.headers.get("Origin") || "";
    if (origin && origin !== allowedOrigin(env)) {
      return settle({ ok: false, error: "Not allowed from this origin." }, 403);
    }
    const ctype = (request.headers.get("Content-Type") || "").toLowerCase();
    if (!ctype.startsWith("application/json")) {
      return settle({ ok: false, error: "Malformed request." }, 400);
    }

    let password = "";
    let company = COMPANIES[0];
    try {
      const body = await request.json();
      password = body.password;
      // Older pages sent no company. Defaulting keeps them working rather
      // than breaking mid-deploy while the new pages are still publishing.
      if (body.company !== undefined && body.company !== null) company = String(body.company);
    } catch {
      return settle({ ok: false, error: "Malformed request." }, 400);
    }

    if (!sameSecret(password, env.REFRESH_PASSWORD)) {
      return settle({ ok: false, error: "Wrong password." }, 401);
    }

    company = company.trim().toLowerCase();
    if (!COMPANIES.includes(company)) {
      return settle({ ok: false, error: "Unknown company." }, 400);
    }

    // Two reads, deliberately. The in-flight check needs the newest run of ANY
    // kind, but the spend caps must see only paid ones — 100 is GitHub's
    // maximum page size, and every commit to main produces a free push run, so
    // an unfiltered page fills with free runs and pushes paid ones out of the
    // 30-day window. That made the monthly cap fail OPEN: measured, at three
    // pushes per refresh it allowed 60 refreshes against a limit of 30.
    let runs, dispatches;
    try {
      [runs, dispatches] = await Promise.all([
        recentRuns(env),
        recentRuns(env, "&event=workflow_dispatch"),
      ]);
    } catch {
      return settle(
        { ok: false, error: "Could not check recent refreshes just now. Try again in a minute." },
        503,
      );
    }

    // Deliberately global, not per company. Every run rebuilds and deploys the
    // whole site and commits to the same data/ directory, so two at once would
    // race on the push and the artifact — and this check is what stops a second
    // one from ever being queued behind the first.
    const inFlight = runs[0] ?? null;
    if (inFlight && inFlight.status !== "completed") {
      return settle(
        { ok: false, error: "A refresh is already running. Give it a few minutes." },
        409,
      );
    }

    // Only manually-dispatched runs cost money — the workflow commits its data
    // files back to main, and those push-triggered runs rebuild with
    // --no-gather, which blocks re-drafting as well as searching. Filtered
    // again here rather than trusting the query alone: if the server-side
    // filter ever stopped applying, counting free runs as paid errs towards
    // refusing a refresh, which is the direction to be wrong in.
    const paid = dispatches.filter((r) => r.event === "workflow_dispatch");
    const mine = chargedTo(paid, company);
    const within = (list, ms) => {
      const t = Date.now() - ms;
      return list.filter((r) => Date.parse(r.created_at) > t).length;
    };

    // Per company: one company's refreshing must not exhaust the other's day.
    const spentToday = within(mine, 24 * 60 * 60 * 1000);
    if (spentToday >= MAX_REFRESHES_PER_DAY) {
      return settle(
        {
          ok: false,
          error: `${spentToday} refreshes for ${company} in the last 24 hours; the limit is ${MAX_REFRESHES_PER_DAY} per company. Try again later — the count drops as older ones age out.`,
        },
        429,
      );
    }

    // Shared: both companies bill the same Apify plan and the same Anthropic
    // key, so the monthly ceiling is on the total, not on each of them.
    const spentMonth = within(paid, 30 * 24 * 60 * 60 * 1000);
    if (spentMonth >= MAX_REFRESHES_PER_30_DAYS) {
      return settle(
        {
          ok: false,
          error: `${spentMonth} refreshes across all companies in the last 30 days; the shared limit is ${MAX_REFRESHES_PER_30_DAYS}. It frees up as older ones age out.`,
        },
        429,
      );
    }

    // Pace against the last PAID refresh for THIS company, not the last run of
    // any kind: the workflow commits its data files back to main, and those
    // free rebuilds would otherwise lock the button for ten minutes after
    // every config edit.
    const lastPaid = mine[0] ?? null;
    if (lastPaid) {
      const startedAt = Date.parse(lastPaid.created_at);
      if (!Number.isFinite(startedAt)) {
        return settle(
          { ok: false, error: "Could not check the last refresh time. Try again in a minute." },
          503,
        );
      }
      const minutes = (Date.now() - startedAt) / 60000;
      if (minutes < COOLDOWN_MINUTES) {
        const wait = Math.ceil(COOLDOWN_MINUTES - minutes);
        return settle(
          {
            ok: false,
            error: `${company} was refreshed ${Math.floor(minutes)} min ago. Wait ${wait} more min — each refresh costs money.`,
          },
          429,
        );
      }
    }

    const res = await github(env, `/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: REF, inputs: { mode: "gather", company } }),
    });
    if (!res.ok) {
      console.log("github dispatch failed", res.status, (await res.text()).slice(0, 200));
      const msg =
        res.status === 401 || res.status === 403
          ? "GitHub refused the request. The token may have expired, or GitHub's API limit is temporarily used up."
          : "GitHub could not start the refresh. Try again in a few minutes.";
      return settle({ ok: false, error: msg }, 502);
    }
    return settle({ ok: true, message: "Refresh started. This takes about 5 minutes." }, 202);
  },
};
