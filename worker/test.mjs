// Set BEFORE the worker module is evaluated, so its MIN_RESPONSE_MS reads 0.
// A static `import` is hoisted above every statement in the file, so this has
// to be a dynamic one — with a plain import the flag was set too late and the
// whole suite silently paid the real 4-second delay on every single case.
globalThis.__TEST_FAST__ = true;
const { default: worker } = await import("./refresh-worker.js");

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
    return runsHandler(String(url));
  };
}

// Applies ?event=… the way GitHub does. The worker asks for the paid runs with
// that filter, so a mock that ignored it would hand back push runs where the
// real API never would, and the caps would be tested against the wrong list.
const ok = (runs) => (url = "") =>
  new Response(
    JSON.stringify({
      workflow_runs: url.includes("event=workflow_dispatch")
        ? runs.filter((r) => r.event === "workflow_dispatch")
        : runs,
    }),
    { status: 200 },
  );
const fails = (status) => () => new Response("rate limited", { status });

const iso = (minsAgo) => new Date(Date.now() - minsAgo * 60000).toISOString();

// No display_title by default: that is what every run created before the
// company was recorded looks like, and those must count against EVERY company
// rather than none. The tests below rely on it — an untitled run behaving like
// a bloctopus run is the fail-closed property, not an accident.
const dispatchRun = (minsAgo, status = "completed") => ({
  event: "workflow_dispatch",
  status,
  created_at: iso(minsAgo),
});

const runFor = (slug, minsAgo, status = "completed") => ({
  event: "workflow_dispatch",
  status,
  created_at: iso(minsAgo),
  display_title: `Dashboard gather company:${slug}`,
});

const pushRun = (minsAgo, status = "completed") => ({
  event: "push",
  status,
  created_at: new Date(Date.now() - minsAgo * 60000).toISOString(),
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

// --- daily cap --- (read from the worker, so raising a cap does not
// silently turn its own test into a no-op)
// Resolved against this file, not the shell's cwd, so `node worker/test.mjs`
// from the repo root works as well as `node test.mjs` from inside worker/.
const src = await import("node:fs").then((fs) =>
  fs.readFileSync(new URL("./refresh-worker.js", import.meta.url), "utf8"),
);
const CAP_DAY = Number(src.match(/MAX_REFRESHES_PER_DAY = (\d+)/)[1]);
const CAP_MONTH = Number(src.match(/MAX_REFRESHES_PER_30_DAYS = (\d+)/)[1]);

const atDailyCap = Array.from({ length: CAP_DAY }, (_, i) => dispatchRun(30 + i * 60));
await check(`${CAP_DAY} dispatch runs in 24h -> daily cap refuses`,
  post({ password: "bloctopus" }), ENV, ok(atDailyCap), 429, 0);

const underDailyCap = Array.from({ length: CAP_DAY - 1 }, (_, i) => dispatchRun(30 + i * 60));
await check(`${CAP_DAY - 1} dispatch runs in 24h -> allowed`,
  post({ password: "bloctopus" }), ENV, ok(underDailyCap), 202, 1);

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

// --- cooldown is paced by PAID runs only ---
// The workflow commits its data file back to main, so every refresh is
// followed by free push-triggered rebuilds. Pacing off the newest run of any
// kind meant a config edit locked the button for ten minutes for no reason.
await check("recent push run + old paid run -> allowed",
  post({ password: "bloctopus" }), ENV,
  ok([pushRun(1), dispatchRun(60 * 5)]), 202, 1);

await check("recent push run + paid run inside cooldown -> still refused",
  post({ password: "bloctopus" }), ENV,
  ok([pushRun(1), dispatchRun(2)]), 429, 0);

// --- 30-day cap ---
const spread = (n, startHrs, stepHrs) => Array.from({ length: n }, (_, i) => dispatchRun(60 * (startHrs + i * stepHrs)));
await check(`${CAP_MONTH} dispatch runs in 30d (none in 24h) -> monthly cap refuses`,
  post({ password: "bloctopus" }), ENV, ok(spread(CAP_MONTH, 30, 12)), 429, 0);
await check(`${CAP_MONTH - 1} dispatch runs in 30d -> allowed`,
  post({ password: "bloctopus" }), ENV, ok(spread(CAP_MONTH - 1, 30, 12)), 202, 1);
await check("12 dispatch runs older than 30d -> not counted, allowed",
  post({ password: "bloctopus" }), ENV, ok(spread(12, 24 * 31, 24)), 202, 1);
await check("100 runs returned, all recent -> saturates, fails CLOSED",
  post({ password: "bloctopus" }), ENV, ok(spread(100, 30, 1)), 429, 0);
await check("push runs do not count toward monthly cap",
  post({ password: "bloctopus" }), ENV,
  ok(Array.from({ length: 40 }, (_, i) => ({ event: "push", status: "completed", created_at: iso(60 * (30 + i * 6)) }))), 202, 1);

// ── per-company attribution ──────────────────────────────────────────
// The cooldown and the daily cap are per company; the monthly cap is shared.

await check("bloctopus refreshed 2 min ago -> lemur is NOT in cooldown",
  post({ password: "bloctopus", company: "lemur" }), ENV, ok([runFor("bloctopus", 2)]), 202, 1);
await check("bloctopus refreshed 2 min ago -> bloctopus IS in cooldown",
  post({ password: "bloctopus", company: "bloctopus" }), ENV, ok([runFor("bloctopus", 2)]), 429, 0);

const bloctopusAtCap = Array.from({ length: CAP_DAY }, (_, i) => runFor("bloctopus", 30 + i * 60));
await check(`bloctopus at its daily cap -> lemur still allowed`,
  post({ password: "bloctopus", company: "lemur" }), ENV, ok(bloctopusAtCap), 202, 1);
await check(`bloctopus at its daily cap -> bloctopus refused`,
  post({ password: "bloctopus", company: "bloctopus" }), ENV, ok(bloctopusAtCap), 429, 0);

// The monthly cap is deliberately NOT per company: one Apify plan, one
// Anthropic key. Split evenly, neither company is near its own daily cap.
const sharedMonth = Array.from({ length: CAP_MONTH }, (_, i) =>
  runFor(i % 2 ? "lemur" : "bloctopus", 60 * (30 + i * 12)));
await check(`${CAP_MONTH} refreshes split across both -> shared monthly cap refuses lemur`,
  post({ password: "bloctopus", company: "lemur" }), ENV, ok(sharedMonth), 429, 0);
await check(`${CAP_MONTH} refreshes split across both -> shared monthly cap refuses bloctopus`,
  post({ password: "bloctopus", company: "bloctopus" }), ENV, ok(sharedMonth), 429, 0);

// Attribution that cannot be read must make the caps STRICTER, never laxer.
// This is the failure mode that would otherwise silently uncap spending.
const untitled = Array.from({ length: CAP_DAY }, (_, i) => dispatchRun(30 + i * 60));
await check("runs with no company in the title -> counted against every company",
  post({ password: "bloctopus", company: "lemur" }), ENV, ok(untitled), 429, 0);
await check("a title naming an unknown company -> counted against every company",
  post({ password: "bloctopus", company: "lemur" }), ENV,
  ok(Array.from({ length: CAP_DAY }, (_, i) => ({
    event: "workflow_dispatch", status: "completed", created_at: iso(30 + i * 60),
    display_title: "Dashboard gather company:acme",
  }))), 429, 0);

// A run already in flight blocks BOTH companies: every run rebuilds and
// deploys the whole site and commits to the same data/ directory.
await check("bloctopus run in flight -> lemur refused too",
  post({ password: "bloctopus", company: "lemur" }), ENV,
  ok([runFor("bloctopus", 1, "in_progress")]), 409, 0);

// ── company validation ───────────────────────────────────────────────
await check("unknown company -> 400, no dispatch",
  post({ password: "bloctopus", company: "acme" }), ENV, ok([]), 400, 0);
await check("company is checked AFTER the password, so it leaks nothing",
  post({ password: "wrong", company: "acme" }), ENV, ok([]), 401, 0);
await check("company omitted (older cached page) -> defaults, still works",
  post({ password: "bloctopus" }), ENV, ok([]), 202, 1);
await check("company with odd case/space -> normalised",
  post({ password: "bloctopus", company: " Lemur " }), ENV, ok([]), 202, 1);

// ── empty-password bypass ────────────────────────────────────────────
// Two empty strings compare equal, so an unset secret used to accept "".
await check("empty password vs real secret -> 401",
  post({ password: "" }), ENV, ok([]), 401, 0);
await check("empty password vs empty secret -> 401, never 202",
  post({ password: "" }), { ...ENV, REFRESH_PASSWORD: "" }, ok([]), 500, 0);

// ── the spend caps must not be evaded by free runs ───────────────────
// GitHub returns at most 100 runs per page, and every commit to main makes a
// free push run. Reading one unfiltered page let free runs push paid ones out
// of the 30-day window: at three pushes per refresh this allowed 60 refreshes
// against a cap of 30. The caps now query event=workflow_dispatch separately.
{
  const paidRuns = Array.from({ length: CAP_MONTH + 5 }, (_, i) =>
    runFor(i % 2 ? "lemur" : "bloctopus", 60 * (30 + i * 20)));
  const pushNoise = Array.from({ length: 300 }, (_, i) =>
    ({ event: "push", status: "completed", created_at: iso(30 + i * 4) }));
  // Exactly what GitHub does: newest first, one page, hard cap of 100.
  const page = (list) => list
    .slice()
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    .slice(0, 100);

  dispatched = 0;
  globalThis.fetch = async (url, init) => {
    if (String(url).includes("/dispatches")) {
      dispatched++;
      return new Response(null, { status: 204 });
    }
    const filtered = String(url).includes("event=workflow_dispatch")
      ? page(paidRuns)
      : page([...paidRuns, ...pushNoise]);
    return new Response(JSON.stringify({ workflow_runs: filtered }), { status: 200 });
  };
  const res = await worker.fetch(post({ password: "bloctopus", company: "lemur" }), ENV);
  const body = await res.json().catch(() => ({}));
  results.push({
    name: "300 free push runs cannot hide paid runs from the monthly cap",
    pass: res.status === 429 && dispatched === 0,
    got: `${res.status}/d=${dispatched}`, want: "429/d=0", msg: body.error || "",
  });
}

// The dispatch must name the company, or the workflow gathers for the wrong one.
{
  let sentBody = null;
  globalThis.fetch = async (url, init) => {
    if (String(url).includes("/dispatches")) {
      sentBody = JSON.parse(init.body);
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify({ workflow_runs: [] }), { status: 200 });
  };
  await worker.fetch(post({ password: "bloctopus", company: "lemur" }), ENV);
  results.push({
    name: "dispatch passes the company through to the workflow",
    pass: sentBody?.inputs?.company === "lemur" && sentBody?.inputs?.mode === "gather",
    got: JSON.stringify(sentBody?.inputs), want: '{"mode":"gather","company":"lemur"}', msg: "",
  });
}

let failed = 0;
for (const r of results) {
  if (!r.pass) failed++;
  console.log(`${r.pass ? "PASS" : "FAIL"}  ${r.name}\n        got ${r.got}  want ${r.want}${r.msg ? `\n        msg: "${r.msg}"` : ""}`);
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
