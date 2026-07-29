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
 * Deploy: Cloudflare Workers (free tier is far more than enough).
 * Secrets:  GITHUB_TOKEN       fine-grained PAT, Actions: read+write, this repo only
 *           REFRESH_PASSWORD   the shared password
 * Vars:     GITHUB_REPO        e.g. milosevic16/linkedin-searches
 *           ALLOWED_ORIGIN     e.g. https://milosevic16.github.io
 */

const WORKFLOW = "dashboard.yml";
const REF = "main";

// A refresh costs real money at two vendors. The password is shared and
// simple by design, so assume it will get around: this is what stops someone
// holding down the button and draining the month's credit.
const COOLDOWN_MINUTES = 10;

// Deliberately slow to answer. A four-second floor makes guessing the password
// tedious without being noticeable to someone who knows it.
const MIN_RESPONSE_MS = 4000;

function cors(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Cache-Control": "no-store",
  };
}

function reply(body, status, env) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...cors(env) },
  });
}

/** Compare in constant time, so the response time cannot reveal how much of
 *  the password was right. */
function sameSecret(a, b) {
  const x = new TextEncoder().encode(String(a ?? ""));
  const y = new TextEncoder().encode(String(b ?? ""));
  if (x.length !== y.length) return false;
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

async function github(env, path, init = {}) {
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "linkedin-radar-refresh",
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
  return res;
}

/** The most recent run, used both for the cooldown and to tell the caller
 *  something is already happening. */
async function latestRun(env) {
  const res = await github(env, `/actions/workflows/${WORKFLOW}/runs?per_page=1`);
  if (!res.ok) return null;
  const data = await res.json();
  return data.workflow_runs?.[0] ?? null;
}

export default {
  async fetch(request, env) {
    const started = Date.now();
    const settle = async (body, status) => {
      // Pad every answer to the same floor so timing says nothing.
      const wait = MIN_RESPONSE_MS - (Date.now() - started);
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
      return reply(body, status, env);
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(env) });
    }
    if (request.method === "GET") {
      // Status only — no secrets, no side effects, so no password needed.
      const run = await latestRun(env);
      return reply(
        {
          running: run ? run.status !== "completed" : false,
          last_run: run?.created_at ?? null,
          conclusion: run?.conclusion ?? null,
        },
        200,
        env,
      );
    }
    if (request.method !== "POST") {
      return reply({ ok: false, error: "Method not allowed." }, 405, env);
    }

    if (!env.GITHUB_TOKEN || !env.REFRESH_PASSWORD || !env.GITHUB_REPO) {
      return reply({ ok: false, error: "Refresh is not configured yet." }, 500, env);
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

    const run = await latestRun(env);
    if (run && run.status !== "completed") {
      return settle(
        { ok: false, error: "A refresh is already running. Give it a few minutes." },
        409,
      );
    }
    if (run) {
      const minutes = (Date.now() - Date.parse(run.created_at)) / 60000;
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
      const detail = (await res.text()).slice(0, 200);
      return settle(
        { ok: false, error: `GitHub refused the request (${res.status}). ${detail}` },
        502,
      );
    }
    return settle({ ok: true, message: "Refresh started. This takes about 5 minutes." }, 202);
  },
};
