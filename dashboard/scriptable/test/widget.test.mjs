// Behavioural tests for the HYDRA iOS widget.
//
// The widget had never run successfully: the shared DASHBOARD_API_KEY it
// authenticated with was retired on 2026-08-03 and nothing replaced it, so
// from then until 2026-10-22 every poll got a 401 and the widget rendered it
// as "HYDRA Offline". Six defects were found when it was finally exercised;
// each is pinned below, with the pre-fix behaviour as a control.
import assert from "node:assert/strict";
import { render } from "./widget_harness.mjs";

const PAYLOAD = {
  status: "monitoring", market_open: true, net_pnl: -500.0, gross_pnl: -460.0,
  credit: 1200.0, commission: 40.0, entries: 3, stops: 1,
  entry_dots: ["expired", "stopped", "skipped", "active", "pending", "pending"],
  cumulative_pnl: -43.65, date: "2026-10-22",
  summary: "...", contracts_per_entry: 7,
};
const p = (o) => ({ ...PAYLOAD, ...o });

// 2026-10-22T02:00:00Z => Lisbon 03:00 on the 22nd, New York 22:00 on the 21st.
const NIGHT = "2026-10-22T02:00:00Z";
const MIDDAY = "2026-10-22T16:00:00Z";   // same date in both zones

let pass = 0, fail = 0;
async function t(name, fn) {
  try { await fn(); pass++; console.log(`  ok   ${name}`); }
  catch (e) { fail++; console.log(`  FAIL ${name}\n       ${e.message}`); }
}

console.log("\n#1 a loss must carry its sign");
await t("a $500 loss renders as −$500, not $500", async () => {
  const r = await render({ body: p({ net_pnl: -500 }), nowISO: MIDDAY });
  assert.ok(r.all.includes("−$500"), r.all);
  assert.ok(!/(^|[^−+])\$500/.test(r.all.replace("−$500", "")), "bare $500 present");
});
await t("a gain renders as +$500", async () => {
  const r = await render({ body: p({ net_pnl: 500 }), nowISO: MIDDAY });
  assert.ok(r.all.includes("+$500"), r.all);
});
await t("flat renders $0 with no sign", async () => {
  const r = await render({ body: p({ net_pnl: 0 }), nowISO: MIDDAY });
  assert.ok(r.all.includes("$0") && !r.all.includes("+$0") && !r.all.includes("−$0"), r.all);
});
await t("CONTROL: the old expression dropped the minus", () => {
  const pnl = -500, old = `${pnl >= 0 ? "+" : ""}$${Math.abs(pnl).toFixed(0)}`;
  assert.equal(old, "$500");   // indistinguishable from a gain but for colour
});

console.log("\n#2 stale data must be visibly stale");
await t("yesterday's payload is flagged with its date", async () => {
  const r = await render({ body: p({ date: "2026-10-20" }), nowISO: MIDDAY });
  assert.ok(r.all.includes("⚠ 2026-10-20"), r.all);
  assert.ok(r.all.includes("cached"), r.all);
});
await t("today's payload shows the live market dot instead", async () => {
  const r = await render({ body: p({ date: "2026-10-22" }), nowISO: MIDDAY });
  assert.ok(!r.all.includes("⚠"), r.all);
  assert.ok(r.all.includes("●"), r.all);
});
await t("data served from CACHE is flagged even if its date matches", async () => {
  const r = await render({ throwOnFetch: true, nowISO: MIDDAY,
                           cache: { at: 1, data: p({ date: "2026-10-22" }) } });
  assert.ok(r.all.includes("cached"), r.all);
});

console.log("\n#2b freshness is judged in EXCHANGE time, not the phone's");
await t("22:00 ET (=03:00 Lisbon next day): an ET-dated payload is FRESH", async () => {
  // The trap: the phone's local date is already 2026-10-22 while the trading
  // day is still 2026-10-21. Using the phone's date would mark every evening
  // stale — and the operator is in Lisbon, so that is every session.
  const r = await render({ body: p({ date: "2026-10-21" }), nowISO: NIGHT });
  assert.ok(!r.all.includes("⚠"), `marked stale in ET evening: ${r.all}`);
});
await t("CONTROL: the phone's own date differs from ET at that instant", () => {
  const d = new Date(NIGHT);
  const lis = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon",
    year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
  const ny = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
  assert.equal(lis, "2026-10-22");
  assert.equal(ny, "2026-10-21");
  assert.notEqual(lis, ny);   // so the test above is not vacuous
});

console.log("\n#3 auth failure must not masquerade as 'offline'");
await t("a 401 with no cache says the token was rejected", async () => {
  const r = await render({ httpStatus: 401, body: { detail: "Not authenticated" } });
  assert.ok(/rejected/i.test(r.all), r.all);
  assert.ok(!/offline|unreachable/i.test(r.all), r.all);
});
await t("a 401 does NOT render as a confident $0", async () => {
  // loadJSON resolves on a JSON 4xx body, so without the status check the
  // {detail:...} payload becomes a payload with every field missing -> "+$0".
  const r = await render({ httpStatus: 401, body: { detail: "Not authenticated" } });
  assert.ok(!r.all.includes("$0"), `rendered a fake zero: ${r.all}`);
});
await t("a 401 WITH a cache shows the numbers and says why they are frozen", async () => {
  const r = await render({ httpStatus: 401, body: { detail: "x" },
                           nowISO: MIDDAY, cache: { at: 1, data: p({}) } });
  assert.ok(r.all.includes("−$500"), r.all);
  assert.ok(/token rejected/i.test(r.all), r.all);
});
await t("a real network failure says unreachable, not rejected", async () => {
  const r = await render({ throwOnFetch: true });
  assert.ok(/unreachable/i.test(r.all), r.all);
  assert.ok(!/rejected/i.test(r.all), r.all);
});
await t("an unset token says so instead of looking broken", async () => {
  const r = await render({ token: "" });
  assert.ok(/token needed/i.test(r.all), r.all);
});
await t("an unset token makes NO network request", async () => {
  const r = await render({ token: "", httpStatus: 500 });
  assert.equal(r.headers, undefined);
});

console.log("\n#4 a skipped entry must not look like a pending one");
await t("skipped is amber; pending is grey", async () => {
  // market_open:false so the header's state dot is the hollow "○" and the
  // filled "●" are the entry dots ONLY. An earlier version of this test used
  // market_open:true and silently indexed the market dot as entry #1.
  const r = await render({ body: p({ market_open: false }), nowISO: MIDDAY });
  const dots = r.texts.filter(x => x.text === "●");
  const colors = dots.map(d => d.color);
  assert.equal(colors.length, 6, "expected exactly the 6 entry dots");
  // order: expired, stopped, skipped, active, pending, pending
  const skipped = colors[2], pending = colors[4];
  assert.notEqual(skipped, pending, "skipped renders identically to pending");
  assert.equal(skipped, "#e3b341");
});
await t("every documented disposition has its own colour", async () => {
  const r = await render({ nowISO: MIDDAY, body: p({ market_open: false,
    entry_dots: ["expired", "stopped", "failed", "skipped", "active", "pending"] }) });
  const c = r.texts.filter(x => x.text === "●").map(d => d.color);
  assert.equal(c.length, 6, "market dot leaked into the entry dots");
  assert.equal(c[0], "#7ee8c7");                 // expired = kept credit
  assert.equal(c[1], "#f85149");                 // stopped
  assert.equal(c[2], "#f85149");                 // failed  = also alarming
  assert.equal(c[3], "#e3b341");                 // skipped = a decision
  assert.equal(c[4], "#58a6ff");                 // active
  assert.equal(c[5], "#5e6e82");                 // pending
  assert.notEqual(c[0], c[3]);                   // the 2026-07-22 distinction
});

console.log("\n#5/#6 fields the backend added FOR this widget are used");
await t("lifetime P&L is shown, signed", async () => {
  const r = await render({ body: p({ cumulative_pnl: -43.65 }), nowISO: MIDDAY });
  assert.ok(r.all.includes("lifetime −$44"), r.all);
});
await t("a missing lifetime shows a dash, not a fabricated 0", async () => {
  const r = await render({ body: p({ cumulative_pnl: null }), nowISO: MIDDAY });
  assert.ok(r.all.includes("lifetime —"), r.all);
});
await t("the [Nc] contracts badge appears at 7 contracts", async () => {
  const r = await render({ body: p({ contracts_per_entry: 7 }), nowISO: MIDDAY });
  assert.ok(r.all.includes("7c"), r.all);
});
await t("no badge at 1 contract (it would be noise)", async () => {
  const r = await render({ body: p({ contracts_per_entry: 1 }), nowISO: MIDDAY });
  assert.ok(!/\b1c\b/.test(r.all), r.all);
});

console.log("\nmisc");
await t("the token travels as a header, never in the URL", async () => {
  const r = await render({ token: "SEKRET", body: p({}), nowISO: MIDDAY });
  assert.equal(r.headers["X-Widget-Token"], "SEKRET");
});
await t("singular/plural read correctly", async () => {
  const r = await render({ body: p({ entries: 1, stops: 1 }), nowISO: MIDDAY });
  assert.ok(r.all.includes("1 entry") && r.all.includes("1 stop"), r.all);
  const r2 = await render({ body: p({ entries: 2, stops: 0 }), nowISO: MIDDAY });
  assert.ok(r2.all.includes("2 entries") && r2.all.includes("0 stops"), r2.all);
});
await t("a successful fetch refreshes the cache", async () => {
  const r = await render({ body: p({ net_pnl: 123 }), nowISO: MIDDAY });
  assert.ok(JSON.parse(r.cached).data.net_pnl === 123);
});
await t("the pre-2026-10-22 bare cache shape still loads", async () => {
  const r = await render({ throwOnFetch: true, nowISO: MIDDAY, cache: p({}) });
  assert.ok(r.all.includes("−$500"), `old cache unreadable: ${r.all}`);
});

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
