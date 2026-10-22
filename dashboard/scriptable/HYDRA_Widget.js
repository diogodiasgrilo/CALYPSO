// HYDRA Widget for iOS Scriptable
// Today's P&L, entry status and lifetime, on the home screen.
//
// ── SETUP ──────────────────────────────────────────────────────────────────
// 1. Install Scriptable from the App Store.
// 2. New script, paste this whole file, name it "HYDRA".
// 3. Issue a widget token on the VM and paste it into WIDGET_TOKEN below:
//      sudo -u calypso bash -c 'cd /opt/calypso && .venv/bin/python -m \
//        scripts.manage_dashboard_users widget-token issue <username> \
//        --label iphone-home-screen'
//    Shown ONCE (only its hash is stored). Revoke any time with
//      ... widget-token revoke <prefix>
// 4. Long-press the home screen → + → Scriptable → Small (or Medium) →
//    tap the widget → Script: HYDRA. Leave "When Interacting" as Open URL
//    (blank) so a tap opens the dashboard.
//
// Run the script INSIDE Scriptable once first — it renders a preview and, more
// usefully, tells you exactly what is wrong if anything is (bad token, no
// network, stale data). The widget surfaces the same states, just smaller.
// ───────────────────────────────────────────────────────────────────────────

const DASHBOARD_URL = "https://domytrade.com";

// Scoped, read-only, revocable. Accepted by /api/widget and nothing else.
//
// Sent as a HEADER, deliberately. nginx's combined log format records the full
// request line, so the old `?api_key=` shape would write this credential to
// disk in plaintext on a public host on every poll the widget ever makes.
//
// Replaces the long-dead `API_KEY` constant: the shared DASHBOARD_API_KEY was
// retired by the 2026-08-03 per-account migration, so from that date until
// 2026-10-22 this widget received {"detail":"Not authenticated"} on every call
// and reported it as "HYDRA Offline".
const WIDGET_TOKEN = "";

const CACHE_KEY = "hydra-widget-cache";

const C = {
  bg: "#2d353f",
  accent: "#7ee8c7",
  loss: "#f85149",
  muted: "#8b9bb0",
  dim: "#5e6e82",
  active: "#58a6ff",
  warn: "#e3b341",
};

// ── data ───────────────────────────────────────────────────────────────────

// Returns {data, source, problem}:
//   source  "live" | "cache" | null
//   problem null | "no-token" | "rejected" | "unreachable"
//
// `problem` exists because the previous version funnelled EVERY failure into
// one catch and rendered them all as "HYDRA Offline". A rejected token and a
// dead network are completely different situations — one needs a new token,
// the other needs nothing — and collapsing them is how this widget stayed
// broken from 2026-08-03 to 2026-10-22 without anyone noticing.
async function fetchData() {
  if (!WIDGET_TOKEN) {
    return { data: readCache(), source: readCache() ? "cache" : null,
             problem: "no-token" };
  }

  const req = new Request(`${DASHBOARD_URL}/api/widget`);
  req.headers = { "X-Widget-Token": WIDGET_TOKEN };
  req.timeoutInterval = 10;

  try {
    const data = await req.loadJSON();
    // loadJSON does NOT throw on a 4xx that returns a JSON body — FastAPI's
    // 401 is {"detail":"Not authenticated"}, which would otherwise be treated
    // as a successful fetch of a payload with every field missing, i.e. a
    // confident "+$0". Check the status explicitly.
    const status = req.response ? req.response.statusCode : 200;
    if (status === 401 || status === 403 || (data && data.detail)) {
      return { data: readCache(), source: readCache() ? "cache" : null,
               problem: "rejected" };
    }
    try {
      Keychain.set(CACHE_KEY, JSON.stringify({ at: Date.now(), data: data }));
    } catch (e) {
      // Caching is a convenience; never fail the render over it.
    }
    return { data: data, source: "live", problem: null };
  } catch (e) {
    return { data: readCache(), source: readCache() ? "cache" : null,
             problem: "unreachable" };
  }
}

function readCache() {
  try {
    const raw = JSON.parse(Keychain.get(CACHE_KEY));
    // Tolerate the pre-2026-10-22 cache shape, which stored the payload bare
    // with no timestamp.
    return raw && raw.data ? raw.data : raw;
  } catch (e) {
    return null;
  }
}

// The trading day in EXCHANGE time. Must not use the phone's local date: this
// phone lives in Lisbon, 5h ahead of ET, so at 01:00 local it is still 20:00
// ET on the PREVIOUS trading day — comparing against local would mark a
// perfectly fresh payload stale every single evening.
function etToday() {
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York",
      year: "numeric", month: "2-digit", day: "2-digit",
    }).format(new Date());
  } catch (e) {
    return null;  // unknown beats a false staleness warning
  }
}

// ── render ─────────────────────────────────────────────────────────────────

function row(w) {
  const s = w.addStack();
  s.layoutHorizontally();
  s.centerAlignContent();
  return s;
}

function label(stack, text, size, color) {
  const t = stack.addText(text);
  t.font = Font.systemFont(size);
  t.textColor = new Color(color);
  return t;
}

function notice(w, title, detail) {
  const t = w.addText(title);
  t.font = Font.boldSystemFont(15);
  t.textColor = new Color(C.warn);
  w.addSpacer(4);
  const d = w.addText(detail);
  d.font = Font.systemFont(11);
  d.textColor = new Color(C.muted);
  d.minimumScaleFactor = 0.8;
}

function createWidget(res) {
  const w = new ListWidget();
  w.backgroundColor = new Color(C.bg);
  w.setPadding(12, 14, 12, 14);
  w.url = DASHBOARD_URL;

  const data = res.data;

  // Nothing to show at all — say WHICH nothing.
  if (!data) {
    if (res.problem === "no-token") {
      notice(w, "Token needed",
             "Set WIDGET_TOKEN in this script. Issue one with:\n" +
             "manage_dashboard_users widget-token issue");
    } else if (res.problem === "rejected") {
      notice(w, "Token rejected",
             "The dashboard refused this token (401). It may be revoked, or " +
             "the account disabled. Issue a new one.");
    } else {
      notice(w, "Unreachable",
             `No response from ${DASHBOARD_URL.replace("https://", "")} ` +
             `within 10s, and no cached data yet.`);
    }
    return w;
  }

  // ── title row: name · [Nc] badge · freshness ──
  const head = row(w);
  const title = head.addText("HYDRA");
  title.font = Font.boldMonospacedSystemFont(13);
  title.textColor = new Color(C.accent);

  // contracts badge — the backend exposes contracts_per_entry expressly for
  // this ("so iOS widget can show a [Nc] badge"); it went unused until now.
  const contracts = data.contracts_per_entry || 0;
  if (contracts > 1) {
    head.addSpacer(5);
    label(head, `${contracts}c`, 10, C.dim);
  }

  head.addSpacer();

  // Freshness, not market hours. The old version showed a filled/hollow dot
  // for market_open, which a reader naturally reads as "is this live?" — so a
  // week-old cached figure looked current whenever the market happened to be
  // open. Staleness now wins the slot; market state moves to the footer.
  const today = etToday();
  const stale = res.source === "cache" ||
                (today && data.date && data.date !== today);
  if (stale) {
    label(head, data.date ? `⚠ ${data.date}` : "⚠ stale", 9, C.warn);
  } else {
    const dot = head.addText(data.market_open ? "●" : "○");
    dot.font = Font.systemFont(8);
    dot.textColor = new Color(data.market_open ? C.accent : C.dim);
  }

  w.addSpacer(6);

  // ── today's net P&L ──
  // A negative number MUST carry its sign. The previous version emitted
  // `${pnl >= 0 ? "+" : ""}$${Math.abs(pnl)}`, so a $500 LOSS rendered as
  // "$500" — distinguishable from a $500 gain only by colour, which is
  // exactly the information a glanceable widget should not hide in a hue.
  const pnl = data.net_pnl || 0;
  const sign = pnl > 0 ? "+" : pnl < 0 ? "−" : "";
  const big = w.addText(`${sign}$${Math.abs(pnl).toFixed(0)}`);
  big.font = Font.boldMonospacedSystemFont(28);
  big.textColor = new Color(pnl < 0 ? C.loss : pnl > 0 ? C.accent : C.muted);
  big.minimumScaleFactor = 0.5;

  w.addSpacer(4);

  // ── entry dots ──
  const dots = row(w);
  dots.spacing = 4;
  const dotColors = {
    active: C.active,
    expired: C.accent,
    stopped: C.loss,
    // A genuine order-execution FAILURE (2026-07-31) — same red as stopped so
    // it reads as "something went wrong", not as a routine non-event.
    failed: C.loss,
    // SKIPPED was missing here and fell through to the pending grey, so six
    // skips at 15:00 looked like a day that had not started yet. The backend
    // separates the two deliberately (dashboard audit 2026-07-22) because
    // "expired" reads as a kept-credit win. Amber = a decision was made.
    skipped: C.warn,
    pending: C.dim,
  };
  (data.entry_dots || []).forEach((s) => {
    const d = dots.addText("●");
    d.font = Font.systemFont(10);
    d.textColor = new Color(dotColors[s] || C.dim);
  });

  w.addSpacer(4);

  // ── stats ──
  const stats = row(w);
  const n = data.entries || 0;
  const st = data.stops || 0;
  label(stats, `${n} ${n === 1 ? "entry" : "entries"}`, 10, C.muted);
  stats.addSpacer();
  label(stats, `${st} ${st === 1 ? "stop" : "stops"}`, 10,
        st > 0 ? C.loss : C.muted);

  w.addSpacer(3);

  // ── lifetime ──
  // cumulative_pnl is rebased server-side precisely so this matches the web
  // dashboard's cumulative card; the two disagreed by ~$1,966 until the
  // 2026-07-22 fix, and the widget then ignored the result of that fix.
  const foot = row(w);
  const cum = data.cumulative_pnl;
  if (cum === undefined || cum === null) {
    label(foot, "lifetime —", 9, C.dim);
  } else {
    const cs = cum > 0 ? "+" : cum < 0 ? "−" : "";
    label(foot, `lifetime ${cs}$${Math.abs(cum).toFixed(0)}`, 9, C.dim);
  }
  foot.addSpacer();
  label(foot, stale ? "cached" : data.market_open ? "open" : "closed", 9, C.dim);

  // A token the server rejected, while cached data is still displayable: show
  // the numbers AND the reason they are frozen, rather than silently serving
  // an ageing cache forever (the 10-week failure mode).
  if (res.problem === "rejected") {
    w.addSpacer(3);
    const warn = w.addText("token rejected");
    warn.font = Font.systemFont(9);
    warn.textColor = new Color(C.warn);
  }

  const refresh = new Date();
  refresh.setMinutes(refresh.getMinutes() + 15);
  w.refreshAfterDate = refresh;

  return w;
}

const res = await fetchData();
const widget = createWidget(res);

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  // Running it by hand inside Scriptable: say plainly what happened, because
  // the widget has only a few words of room for it.
  console.log(`source : ${res.source || "none"}`);
  console.log(`problem: ${res.problem || "none"}`);
  console.log(`ET today: ${etToday()}  payload date: ${res.data ? res.data.date : "-"}`);
  if (res.data) console.log(JSON.stringify(res.data, null, 2));
  widget.presentSmall();
}

Script.complete();
