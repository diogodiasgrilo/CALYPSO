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


// Source assertions must read EXECUTABLE lines only. Four separate tests in
// this file have now matched a comment that documents the very bug being
// asserted against — `?api_key=`, `Font.monospacedSystemFont`, and twice on
// retired colour values. A comment explaining a fix is not the fix regressing.
import fs from "node:fs";
const WIDGET_SRC = fs.readFileSync("dashboard/scriptable/HYDRA_Widget.js", "utf8");
const widgetCode = () =>
  WIDGET_SRC.split("\n")
            .filter(l => !l.trim().startsWith("//") && !l.trim().startsWith("*"))
            .join("\n");

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
  assert.equal(skipped, "#FFD60A");   // systemYellow dark
});
await t("every documented disposition has its own colour", async () => {
  const r = await render({ nowISO: MIDDAY, body: p({ market_open: false,
    entry_dots: ["expired", "stopped", "failed", "skipped", "active", "pending"] }) });
  const c = r.texts.filter(x => x.text === "●").map(d => d.color);
  assert.equal(c.length, 6, "market dot leaked into the entry dots");
  assert.equal(c[0], "#30D158");                 // systemGreen — kept credit
  assert.equal(c[1], "#FF453A");                 // systemRed  — stopped
  assert.equal(c[2], "#FF453A");                 // systemRed  — failed
  assert.equal(c[3], "#FFD60A");                 // systemYellow — a decision
  assert.equal(c[4], "#0A84FF");                 // systemBlue — active
  assert.equal(c[5], "#5A5A5F");                 // tertiaryLabel — pending
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


// ───────────────────────────────────────────────────────────────────────────
// Fleet view (Large / Medium), added 2026-10-22.
//
// /api/widget serves the LIVE SEAT only — how it was written in March — which
// made the home screen a very narrow window on a nine-strategy fleet. The fleet
// view shows all of them, grouped by COMPARABILITY, with no global #1.
//
// That last part is a data property, not taste. On the real payload below,
// a global leaderboard would crown H at +$139.80 — but H trades SPY at one
// contract (~1/70th of bl's notional at 7 SPX contracts) and its entire figure
// predates the range-expansion filter it now runs, so it has zero observations
// under current rules. Meanwhile bl's lifetime covers 9 days and c's covers 94.
// ───────────────────────────────────────────────────────────────────────────

// Captured verbatim from https://domytrade.com/api/widget/fleet on 2026-10-22.
const FLEET = {
  date: "2026-10-22", market_open: false,
  live: { id: "bl", name: "Brandon Late", today: 0.0, lifetime: -43.65, days: 9, live: true, status: "live" },
  groups: [
    { id: "ic_0dte", label: "0DTE Iron Condor", pnl_shape: "credit", comparable: true, rows: [
      { id: "a",  name: "Baseline",        today: 0.0, lifetime: 674.0,     days: 95, live: false, status: "dry_run_shadow" },
      { id: "bl", name: "Brandon Late",    today: 0.0, lifetime: -43.65,    days: 9,  live: true,  status: "live" },
      { id: "f",  name: "Ghauri",          today: 0.0, lifetime: -171.1,    days: 40, live: false, status: "dry_run_locked" },
      { id: "b",  name: "B-A",             today: 0.0, lifetime: -3327.3,   days: 64, live: false, status: "dry_run_shadow" },
      { id: "c",  name: "Brandon Narrow",  today: 0.0, lifetime: -12244.78, days: 94, live: false, status: "dry_run_shadow" }]},
    { id: "calendar_multiday", label: "Multi-day Calendar", pnl_shape: "debit", comparable: true, rows: [
      { id: "e", name: "Double Calendar", today: 0.0, lifetime: -92.85,   days: 20, live: false, status: "dry_run_locked" },
      { id: "d", name: "Time Machine",    today: 0.0, lifetime: -6307.75, days: 73, live: false, status: "dry_run_locked" }]},
    { id: "undefined_risk_0dte", label: "Undefined-Risk 0DTE", pnl_shape: "credit", comparable: false, rows: [
      { id: "g", name: "Strangle", today: 0.0, lifetime: -310.55, days: 40, live: false, status: "dry_run_locked" }]},
    { id: "long_gamma_0dte", label: "Long-Gamma 0DTE", pnl_shape: "debit", comparable: false, rows: [
      { id: "h", name: "Long Strangle", today: 0.0, lifetime: 139.8, days: 21, live: false, status: "dry_run_locked" }]},
  ],
};
const f = (o) => ({ ...FLEET, ...o });

console.log("\n#7 the right endpoint for the right size");
await t("Large fetches the FLEET endpoint", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  assert.ok(r.urls.some(u => u.endsWith("/api/widget/fleet")), r.urls.join());
  assert.ok(!r.urls.some(u => u.endsWith("/api/widget")), "also hit the single-seat endpoint");
});
await t("Small still fetches the single-seat endpoint", async () => {
  const r = await render({ family: "small", body: p({}), nowISO: MIDDAY });
  assert.ok(r.urls.some(u => u.endsWith("/api/widget")), r.urls.join());
  assert.ok(!r.urls.some(u => u.includes("/fleet")), "small should not fetch the fleet");
});
await t("extraLarge (iPad) falls through to the fleet layout", async () => {
  const r = await render({ family: "extraLarge", fleetBody: FLEET, nowISO: MIDDAY });
  assert.ok(r.urls.some(u => u.endsWith("/api/widget/fleet")));
  assert.ok(r.all.includes("HYDRA"));
});

console.log("\n#8 the whole fleet is visible");
await t("every one of the 9 strategies appears", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  for (const n of ["Baseline", "Brandon Late", "Ghauri", "B-A", "Brandon Narrow",
                   "Double Calendar", "Time Machine", "Strangle", "Long Strangle"])
    assert.ok(r.all.includes(n), `${n} missing from the fleet view`);
});
await t("every comparability group is labelled", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  for (const g of ["0DTE Iron Condor", "Multi-day Calendar",
                   "Undefined-Risk 0DTE", "Long-Gamma 0DTE"])
    assert.ok(r.all.includes(g), `${g} missing`);
});
await t("each row carries its day count", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  // 9d for bl against 94d for c is the whole reason lifetimes are not ranked
  for (const d of ["95d", "9d", "40d", "64d", "94d", "20d", "73d", "21d"])
    assert.ok(r.all.includes(d), `${d} missing — lifetime shown without its window`);
});

console.log("\n#9 no global leaderboard");
await t("H (+$140) is NOT presented above the rest", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  const iH = r.all.indexOf("Long Strangle");
  const iA = r.all.indexOf("Baseline");
  assert.ok(iA < iH, "H appears before the IC group — that is a global ranking");
});
await t("ordering is group-scoped, best-first WITHIN each group", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  // Scope the search to the group's own section. A bare indexOf finds
  // "Brandon Late" in the LIVE header line ABOVE the groups and reports a
  // false ordering failure — the same class of mistake as indexing the
  // market-state dot as entry #1.
  const section = (label, nextLabel) => {
    const a = r.all.indexOf(label);
    const b = nextLabel ? r.all.indexOf(nextLabel) : r.all.length;
    assert.ok(a >= 0 && b > a, `section ${label} not found`);
    return r.all.slice(a, b);
  };
  const ic = section("0DTE Iron Condor", "Multi-day Calendar");
  assert.ok(ic.indexOf("Baseline") < ic.indexOf("Brandon Late"), "IC not best-first");
  assert.ok(ic.indexOf("Brandon Late") < ic.indexOf("Brandon Narrow"), "IC not best-first");
  const cal = section("Multi-day Calendar", "Undefined-Risk 0DTE");
  assert.ok(cal.indexOf("Double Calendar") < cal.indexOf("Time Machine"),
            "calendar group not best-first");
});

await t("CONTROL: the live seat name DOES appear above the groups", () => {
  // Proves the scoping above is necessary, not defensive clutter.
  assert.ok(FLEET.live.name === "Brandon Late" &&
            FLEET.groups[0].rows.some(r => r.name === "Brandon Late"),
            "fixture no longer exercises the duplicate-name case");
});
await t("no '#1' / 'winner' / 'best' label anywhere", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  assert.ok(!/#1|winner|best overall/i.test(r.all), r.all);
});

console.log("\n#10 the live seat is distinguishable from 8 simulations");
await t("the LIVE tag is rendered", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  assert.ok(r.all.includes("LIVE"), r.all);
});
await t("the live row is the accent colour, simulated rows are muted", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  const live = r.texts.find(x => x.text === "Brandon Late" && x.color === "#FFFFFF");
  const sim  = r.texts.find(x => x.text === "Brandon Narrow");
  assert.ok(live, "live row not at full label contrast");
  assert.equal(sim.color, "#98989F");   // secondaryLabel
});

console.log("\n#11 amounts keep their sign in the fleet too");
await t("A's +$674 renders with a plus", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  assert.ok(r.all.includes("+$674"), r.all);
});
await t("C's −$12,245 renders with a minus AND a thousands separator", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  assert.ok(r.all.includes("−$12,245"), r.all);
  assert.ok(!r.all.includes("−$12245"), "ungrouped digits still present");
});
await t("thousands grouping applies across the board", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  assert.ok(r.all.includes("−$3,327") && r.all.includes("−$6,308"), r.all);
  assert.ok(r.all.includes("+$674"), "3 digits must NOT be grouped");
});
await t("a null lifetime shows a dash, never a fabricated 0", async () => {
  const body = f({ groups: [{ id: "x", label: "X", pnl_shape: "credit", comparable: true,
    rows: [{ id: "z", name: "Zed", today: 0, lifetime: null, days: 0, live: false, status: "s" }] }] });
  const r = await render({ family: "large", fleetBody: body, nowISO: MIDDAY });
  assert.ok(r.all.includes("—"), r.all);
});

console.log("\n#12 medium shows group leaders only");
await t("one row per group, and it is that group's leader", async () => {
  const r = await render({ family: "medium", fleetBody: FLEET, nowISO: MIDDAY });
  assert.ok(r.all.includes("Baseline"), "IC leader missing");
  assert.ok(!r.all.includes("Brandon Narrow"), "medium rendered a non-leader");
  assert.ok(r.all.includes("Double Calendar") && !r.all.includes("Time Machine"));
});

console.log("\n#13 fleet inherits the freshness + auth guarantees");
await t("a stale fleet payload is flagged with its date", async () => {
  const r = await render({ family: "large", fleetBody: f({ date: "2026-10-20" }), nowISO: MIDDAY });
  assert.ok(r.all.includes("⚠ 2026-10-20"), r.all);
});
await t("a 401 on the fleet says rejected, not offline", async () => {
  const r = await render({ family: "large", httpStatus: 401,
                           fleetBody: { detail: "Not authenticated" } });
  assert.ok(/rejected/i.test(r.all), r.all);
});
await t("the fleet caches under its OWN key, not the small widget's", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  assert.ok(r.cachedFleet, "fleet cache not written");
  assert.ok(JSON.parse(r.cachedFleet).data.groups, "wrong shape cached");
});

console.log("\n#14 presentation — the first rendering was functional, not polished");

await t("content is pushed to the TOP of the tile", async () => {
  // ListWidget CENTRES its content, which left ~45% of the Large tile empty
  // above and below a cramped block. A trailing flexible spacer fixes it.
  const code = widgetCode();
  const body = code.slice(code.indexOf("function createFleetWidget"),
                          code.indexOf("function createWidget("));
  // Must come AFTER the groups are rendered. An earlier version of this test
  // only asserted "a spacer exists before the final return", which the
  // error-branch spacer near the top of the function satisfied — so deleting
  // the real one did not fail anything. Caught by its own mutation control.
  const afterGroups = body.lastIndexOf("groups.forEach(");
  const spacer = body.lastIndexOf("w.addSpacer();");
  const ret = body.lastIndexOf("return w;");
  assert.ok(afterGroups > 0, "fixture stale: groups loop not found");
  assert.ok(spacer > afterGroups && spacer < ret,
            "no flexible spacer AFTER the rows — the tile will stay centred");
});

await t("numbers use a font that ACTUALLY EXISTS in Scriptable", async () => {
  // `Font.monospacedSystemFont` is not a Scriptable API. The first version
  // guarded on it and silently fell through to the proportional font, which is
  // why the columns looked ragged. The harness omits it for the same reason.
  const src = widgetCode();
  assert.ok(!src.includes("Font.monospacedSystemFont"),
            "references a Font API that does not exist");
  assert.ok(src.includes("MonospacedSystemFont"), "no monospaced font at all");
});

await t("numeric columns are FIXED WIDTH so they align", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  // every numeric cell lives in its own sized stack
  assert.ok(r.sizes.some(s2 => s2 && s2.width === 78), "no lifetime column width");
});

await t("an all-zero TODAY column is HIDDEN, and says so", async () => {
  // The single most obvious flaw in the previous rendering was a column of
  // NINE identical "$0". Apple's principle is a small amount of information
  // that is actually informative; a column of placeholders is neither.
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  const zeros = r.texts.filter(x => x.text === "$0");
  assert.equal(zeros.length, 1, `expected only the hero $0, got ${zeros.length}`);
  assert.ok(r.all.includes("no entries yet today"), r.all);
  assert.ok(!r.all.includes("TODAY"), "a heading for a hidden column");
});

await t("once ANYTHING has traded the TODAY column appears", async () => {
  const live = JSON.parse(JSON.stringify(FLEET));
  live.groups[0].rows[1].today = 412.5;     // bl trades
  live.live.today = 412.5;
  const r = await render({ family: "large", fleetBody: live, nowISO: MIDDAY });
  assert.ok(r.all.includes("TODAY"), "column heading missing once active");
  assert.ok(r.all.includes("+$413") || r.all.includes("+$412"), r.all);
  assert.ok(!r.all.includes("no entries yet today"));
});

console.log("\n#15 Apple specifics, verified against the real APIs");

await t("16pt margins, per Apple's widget spec", async () => {
  const body = widgetCode().slice(widgetCode().indexOf("function createFleetWidget"));
  assert.ok(body.includes("setPadding(16, 16, 16, 16)"), "not 16pt margins");
});

await t("typography uses Dynamic Type presets, not hardcoded sizes", async () => {
  // Scriptable exposes the real Apple text styles (caption1/2, footnote,
  // headline, ...). Hardcoding point sizes both looks non-native and ignores
  // the user's accessibility text-size setting.
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  const fonts = r.texts.map(x => x.font).filter(Boolean);
  assert.ok(fonts.includes("headline"), "title is not a text style");
  assert.ok(fonts.includes("caption1") && fonts.includes("caption2"),
            "rows/labels are not text styles");
});

await t("the TITLE is SF Pro; only NUMERALS are monospaced", async () => {
  const r = await render({ family: "large", fleetBody: FLEET, nowISO: MIDDAY });
  const title = r.texts.find(x => x.text === "HYDRA");
  assert.equal(title.font, "headline", "title should not be monospaced");
  const hero = r.texts.find(x => x.text === "$0");
  assert.equal(hero.font, "boldMono", "aligned figures must be monospaced");
});

await t("every colour is a verified iOS system colour", async () => {
  const raw = widgetCode();
  const pal = raw.slice(raw.indexOf("const C = {"), raw.indexOf("const T = {"));
  const hexes = (pal.match(/#[0-9A-Fa-f]{6}/g) || []);
  const allowed = ["#1C1C1E", "#FFFFFF", "#98989F", "#5A5A5F",
                   "#38383A", "#FF453A", "#30D158", "#0A84FF", "#FFD60A"];
  for (const h of hexes)
    assert.ok(allowed.includes(h), `${h} is not an iOS system colour`);
  // and the invented ones are gone
  for (const bad of ["#f85149", "#7ee8c7", "#2d353f", "#8b9bb0", "#e3b341"])
    assert.ok(!raw.includes(bad), `invented colour ${bad} still present`);
});

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
