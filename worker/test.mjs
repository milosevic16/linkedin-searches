import worker from "./refresh-worker.js";

const ENV = {
  GITHUB_TOKEN: "tok",
  REFRESH_PASSWORD: "bloctopus",
  GITHUB_REPO: "milosevic16/linkedin-searches",
  ALLOWED_ORIGIN: "https://milosevic16.github.io",
};

let dispatched = 0;
let runsResponse = null; // set per test

function mockFetch(runsHandler, dispatchStatus = 204) {
  globalThis.fetch = async (url, init) => {
    if (String(url).includes("/dispatches")) {
      dispatched++;
      return new Response(dispatchStatus === 204 ? null : "err", { status: dispatchStatus });
    }
    return runsHandler();
  };
}

const ok = (runs) => () =>
  new Response(JSON.stringify({ workflow_runs: runs }), { status: 200 });
const fails = (status) => () => new Response("rate limited", { status });

const iso = (minsAgo) => new Date(Date.now() - minsAgo * 60000).toISOString();
const dispatchRun = (minsAgo, status = "completed") => ({
  event: "workflow_dispatch",
  status,
  created_at: iso(minsAgo),
});

function post(body, headers = {}) {
  return new Request("https://w.dev/", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Origin: "https://milosevic16.github.io",
      ...headers,
    },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

const results = [];
async function check(name, req, env, handler, expectStatus, expectDispatch) {
  dispatched = 0;
  mockFetch(handler);
  const res = await worker.fetch(req, env);
  const body = await res.json().catch(() => ({}));
  const pass = res.status === expectStatus && dispatched === expectDispatch;
  results.push({ name, pass, got: `${res.status}/d=${dispatched}`, want: `${expectStatus}/d=${expectDispatch}`, msg: body.error || body.message || "" });
}

// --- fail-closed on GitHub read error (was: fail OPEN, dispatched anyway) ---
await check("GitHub 403 on runs read -> refuse, no dispatch",
  post({ password: "bloctopus" }), ENV, fails(403), 503, 0);
await check("GitHub 500 on runs read -> refuse, no dispatch",
  post({ password: "bloctopus" }), ENV, fails(500), 503, 0);

// --- empty run list is a genuine 'no runs', still allowed ---
await check("no runs at all -> dispatch allowed",
  post({ password: "bloctopus" }), ENV, ok([]), 202, 1);

// --- daily cap ---
const threeToday = Array.from({ length: 3 }, (_, i) => dispatchRun(30 + i * 60));
await check("3 dispatch runs in 24h -> daily cap refuses",
  post({ password: "bloctopus" }), ENV, ok(threeToday), 429, 0);

const twoToday = Array.from({ length: 2 }, (_, i) => dispatchRun(30 + i * 60));
await check("2 dispatch runs in 24h -> allowed",
  post({ password: "bloctopus" }), ENV, ok(twoToday), 202, 1);

// cap must ignore push-triggered (free) runs
const pushRuns = Array.from({ length: 20 }, (_, i) => ({
  event: "push", status: "completed", created_at: iso(30 + i * 20),
}));
await check("20 push runs in 24h -> not counted, allowed",
  post({ password: "bloctopus" }), ENV, ok(pushRuns), 202, 1);

// cap must ignore dispatch runs older than 24h
const oldRuns = Array.from({ length: 10 }, (_, i) => dispatchRun(60 * 25 + i * 60));
await check("10 dispatch runs older than 24h -> not counted, allowed",
  post({ password: "bloctopus" }), ENV, ok(oldRuns), 202, 1);

// --- cooldown still works ---
await check("last run 3 min ago -> cooldown refuses",
  post({ password: "bloctopus" }), ENV, ok([dispatchRun(3)]), 429, 0);
await check("last run 11 min ago -> allowed",
  post({ password: "bloctopus" }), ENV, ok([dispatchRun(11)]), 202, 1);

// --- already running blocks, incl. unknown status ---
await check("run in_progress -> 409",
  post({ password: "bloctopus" }), ENV, ok([dispatchRun(1, "in_progress")]), 409, 0);
await check("run with novel status 'pending' -> 409 (fail-safe direction)",
  post({ password: "bloctopus" }), ENV, ok([dispatchRun(20, "pending")]), 409, 0);

// --- malformed timestamp fails closed (was: NaN < 10 === false -> dispatched) ---
await check("unparseable created_at -> refuse, no dispatch",
  post({ password: "bloctopus" }), ENV,
  ok([{ event: "workflow_dispatch", status: "completed", created_at: "not-a-date" }]), 503, 0);

// --- auth ---
await check("wrong password -> 401, no dispatch",
  post({ password: "wrong" }), ENV, ok([]), 401, 0);
await check("no password field -> 401",
  post({}), ENV, ok([]), 401, 0);

// --- CSRF gates ---
await check("text/plain body (CORS-simple, no preflight) -> 400, no dispatch",
  post({ password: "bloctopus" }, { "Content-Type": "text/plain" }), ENV, ok([]), 400, 0);
await check("foreign Origin -> 403, no dispatch",
  post({ password: "bloctopus" }, { Origin: "https://evil.com" }), ENV, ok([]), 403, 0);
await check("no Origin (curl) -> allowed through to normal flow",
  new Request("https://w.dev/", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: "bloctopus" }) }),
  ENV, ok([]), 202, 1);

// --- trailing slash normalisation on ALLOWED_ORIGIN ---
await check("ALLOWED_ORIGIN has trailing slash -> still matches browser Origin",
  post({ password: "bloctopus" }), { ...ENV, ALLOWED_ORIGIN: "https://milosevic16.github.io/" },
  ok([]), 202, 1);

// --- config guard now includes ALLOWED_ORIGIN ---
await check("ALLOWED_ORIGIN unset -> 500 not configured",
  post({ password: "bloctopus" }), { ...ENV, ALLOWED_ORIGIN: "" }, ok([]), 500, 0);

// --- GET branch removed ---
await check("GET -> 405, no GitHub call",
  new Request("https://w.dev/", { method: "GET" }), ENV, fails(403), 405, 0);

// --- no wildcard CORS ---
const optRes = await worker.fetch(new Request("https://w.dev/", { method: "OPTIONS" }), ENV);
const acao = optRes.headers.get("Access-Control-Allow-Origin");
results.push({
  name: "CORS header is never '*'", pass: acao === "https://milosevic16.github.io",
  got: String(acao), want: "https://milosevic16.github.io", msg: "",
});

// --- 30-day cap ---
const spread = (n, startHrs, stepHrs) => Array.from({ length: n }, (_, i) => dispatchRun(60 * (startHrs + i * stepHrs)));
await check("12 dispatch runs in 30d (none in 24h) -> monthly cap refuses",
  post({ password: "bloctopus" }), ENV, ok(spread(12, 30, 24)), 429, 0);
await check("11 dispatch runs in 30d -> allowed",
  post({ password: "bloctopus" }), ENV, ok(spread(11, 30, 24)), 202, 1);
await check("12 dispatch runs older than 30d -> not counted, allowed",
  post({ password: "bloctopus" }), ENV, ok(spread(12, 24 * 31, 24)), 202, 1);
await check("100 runs returned, all recent -> saturates, fails CLOSED",
  post({ password: "bloctopus" }), ENV, ok(spread(100, 30, 1)), 429, 0);
await check("push runs do not count toward monthly cap",
  post({ password: "bloctopus" }), ENV,
  ok(Array.from({ length: 40 }, (_, i) => ({ event: "push", status: "completed", created_at: iso(60 * (30 + i * 6)) }))), 202, 1);

let failed = 0;
for (const r of results) {
  if (!r.pass) failed++;
  console.log(`${r.pass ? "PASS" : "FAIL"}  ${r.name}\n        got ${r.got}  want ${r.want}${r.msg ? `\n        msg: "${r.msg}"` : ""}`);
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
