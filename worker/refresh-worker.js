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

// A refresh costs money at two vendors. The cooldown paces refreshes; the
// caps bound them. Without the caps, someone with the password could trigger
// 144 a day forever against a budget of roughly ten a month.
const COOLDOWN_MINUTES = 10;
const MAX_REFRESHES_PER_DAY = 3;
const MAX_REFRESHES_PER_30_DAYS = 12;

// Deliberately slow to answer, so guessing the password is tedious.
const MIN_RESPONSE_MS = Number(globalThis.__TEST_FAST__ ? 0 : 4000);

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
 *  password was right. */
function sameSecret(a, b) {
  const x = new TextEncoder().encode(String(a ?? ""));
  const y = new TextEncoder().encode(String(b ?? ""));
  if (x.length !== y.length) return false;
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
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
async function recentRuns(env) {
  const res = await github(env, `/actions/workflows/${WORKFLOW}/runs?per_page=100`);
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
    try {
      ({ password } = await request.json());
    } catch {
      return settle({ ok: false, error: "Malformed request." }, 400);
    }

    if (!sameSecret(password, env.REFRESH_PASSWORD)) {
      return settle({ ok: false, error: "Wrong password." }, 401);
    }

    let runs;
    try {
      runs = await recentRuns(env);
    } catch {
      return settle(
        { ok: false, error: "Could not check recent refreshes just now. Try again in a minute." },
        503,
      );
    }

    const run = runs[0] ?? null;
    if (run && run.status !== "completed") {
      return settle(
        { ok: false, error: "A refresh is already running. Give it a few minutes." },
        409,
      );
    }

    // Only manually-dispatched runs cost money — the workflow commits its data
    // file back to main, and those push-triggered runs rebuild with --no-gather.
    const paid = runs.filter((r) => r.event === "workflow_dispatch");
    const since = (ms) => {
      const t = Date.now() - ms;
      return paid.filter((r) => Date.parse(r.created_at) > t).length;
    };
    const spentToday = since(24 * 60 * 60 * 1000);
    if (spentToday >= MAX_REFRESHES_PER_DAY) {
      return settle(
        {
          ok: false,
          error: `That is ${spentToday} refreshes in 24 hours, which is the daily limit. Try again tomorrow.`,
        },
        429,
      );
    }
    const spentMonth = since(30 * 24 * 60 * 60 * 1000);
    if (spentMonth >= MAX_REFRESHES_PER_30_DAYS) {
      return settle(
        {
          ok: false,
          error: `That is ${spentMonth} refreshes in 30 days, which is the monthly limit. It resets as older refreshes age out.`,
        },
        429,
      );
    }

    if (run) {
      const startedAt = Date.parse(run.created_at);
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
            error: `Refreshed ${Math.floor(minutes)} min ago. Wait ${wait} more min — each refresh costs money.`,
          },
          429,
        );
      }
    }

    const res = await github(env, `/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: REF, inputs: { mode: "gather" } }),
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
