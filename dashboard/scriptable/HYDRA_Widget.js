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

const CACHE_KEY = "hydra-widget-cache";       // small: live seat
const FLEET_CACHE_KEY = "hydra-widget-fleet";  // medium/large: whole fleet

// HYDRA's OWN design system, read from dashboard/frontend/src/index.css —
// not Apple's system palette and not an invention.
//
// The previous version replaced these with generic iOS colours (#FF453A /
// #30D158 / #1C1C1E) in pursuit of "Apple-like", which stripped the product's
// identity out entirely. The teal below is the brand: it is the colour of the
// HYDRA mark itself, and `--color-profit` across the whole dashboard. Apple's
// contribution here is STRUCTURE — 16pt margins, Dynamic Type, monospaced
// numerals, show-only-what-is-informative — not colour.
const C = {
  bg: "#1a2229",          // --color-bg
  card: "#222e35",        // --color-card
  elevated: "#2d3b43",    // --color-bg-elevated
  separator: "#3d5058",   // --color-border
  green: "#7ee8c7",       // --color-profit      (the HYDRA teal)
  greenDim: "#5ca18d",    // --color-profit-muted
  red: "#f95f58",         // --color-loss
  redDim: "#cd5049",      // --color-loss-muted
  warn: "#d29922",        // --color-warning
  blue: "#5da8ff",        // --color-info
  label: "#e8edf3",       // --color-text-primary
  label2: "#c2cad5",      // --color-text-secondary
  label3: "#98a5b5",      // --color-text-dim
};

// Apple's Dynamic Type presets. Scriptable exposes the real text styles, and
// the first two versions hardcoded point sizes instead — which both looked
// non-native and ignored the accessibility text-size setting.
const T = {
  headline: () => Font.headline(),
  subheadline: () => Font.subheadline(),
  footnote: () => Font.footnote(),
  caption1: () => Font.caption1(),
  caption2: () => Font.caption2(),
};

// Numerals that must align cannot use a Dynamic Type preset — Scriptable has
// no "monospaced digits of a text style" — so aligned figures use a
// monospaced face at a size paired to the adjacent style. The dashboard's own
// scale bottoms out at 10px (--text-3xs) for dense tabular data; matched here.
const N = { row: 12, hero: 22, head: 10 };

const LOGO_URL = `${DASHBOARD_URL}/hydra-logo-120.png`;
const LOGO_FILE = "hydra-logo-120.png";

// The mark, fetched once and cached on disk. Re-downloading it on every
// 15-minute refresh would be wasteful, and a widget that fails to render
// because an image did not load is worse than one with no image.
async function loadLogo() {
  try {
    const fm = FileManager.local();
    const path = fm.joinPath(fm.cacheDirectory(), LOGO_FILE);
    if (fm.fileExists(path)) return fm.readImage(path);
    const img = await new Request(LOGO_URL).loadImage();
    try { fm.writeImage(path, img); } catch (e) { /* cache is optional */ }
    return img;
  } catch (e) {
    return null;   // render without the mark rather than not at all
  }
}

// A sparkline of the cumulative curve, drawn with DrawContext. Apple's Stocks
// widget does exactly this: the shape carries information a single figure
// cannot — whether a lifetime number is a steady bleed or one bad week.
function sparkline(series, w2, h2, color, scale) {
  if (!series || series.length < 2) return null;
  const ctx = new DrawContext();
  ctx.size = new Size(w2, h2);
  ctx.opaque = false;
  ctx.respectScreenScale = true;
  // PER-ROW scaling: the curve carries SHAPE, the figure beside it carries
  // MAGNITUDE. A group-shared axis was implemented and then MEASURED against
  // live data, and it was worse:
  //
  //   Ghauri          -$171      0.2pt   <- a flat line, no information
  //   Brandon Late     -$44      0.8pt   <- the LIVE SEAT, nearly flat
  //   Brandon Narrow  -$12,245   7.5pt
  //
  // One strategy losing 280x more than another flattened the row you most
  // want to read down to nothing. Magnitude is already unambiguous in the
  // adjacent number; shape is the only thing the curve can add, and a shared
  // axis destroys it for every small-magnitude row. Apple's Stocks widget
  // scales per row for the same reason. `scale` is retained so a caller can
  // still impose an axis where the magnitudes genuinely are of one kind.
  const lo = scale ? scale.lo : Math.min(...series);
  const hi = scale ? scale.hi : Math.max(...series);
  const span = hi - lo || 1;
  const pad = 1.5;
  const pts = series.map((v, i) => new Point(
    (i / (series.length - 1)) * (w2 - 2 * pad) + pad,
    h2 - pad - ((v - lo) / span) * (h2 - 2 * pad)));
  // Zero line, when the curve crosses it — it is the only reference that
  // makes the shape mean anything.
  if (lo < 0 && hi > 0) {
    const y0 = h2 - pad - ((0 - lo) / span) * (h2 - 2 * pad);
    const zp = new Path();
    zp.move(new Point(0, y0));
    zp.addLine(new Point(w2, y0));
    ctx.setStrokeColor(new Color(C.separator));
    ctx.setLineWidth(0.5);
    ctx.addPath(zp);
    ctx.strokePath();
  }
  const path = new Path();
  path.move(pts[0]);
  path.addLines(pts);
  ctx.setStrokeColor(new Color(color));
  ctx.setLineWidth(1.4);
  ctx.addPath(path);
  ctx.strokePath();
  return ctx.getImage();
}
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
async function fetchData(path, cacheKey) {
  if (!WIDGET_TOKEN) {
    const c = readCache(cacheKey);
    return { data: c, source: c ? "cache" : null, problem: "no-token" };
  }

  const req = new Request(`${DASHBOARD_URL}${path}`);
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
      const c = readCache(cacheKey);
      return { data: c, source: c ? "cache" : null, problem: "rejected" };
    }
    try {
      Keychain.set(cacheKey, JSON.stringify({ at: Date.now(), data: data }));
    } catch (e) {
      // Caching is a convenience; never fail the render over it.
    }
    return { data: data, source: "live", problem: null };
  } catch (e) {
    const c = readCache(cacheKey);
    return { data: c, source: c ? "cache" : null, problem: "unreachable" };
  }
}

function readCache(cacheKey) {
  try {
    const raw = JSON.parse(Keychain.get(cacheKey));
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

// TABULAR digits. Scriptable has no `Font.monospacedSystemFont` — the real
// names are regular/medium/boldMonospacedSystemFont — so the first version's
// `Font.monospacedSystemFont ? ... : Font.systemFont(...)` guard silently fell
// through to the PROPORTIONAL font. That is why the first rendering's number
// columns looked ragged: "1" and "7" occupied different widths.
function mono(size, weight) {
  if (weight === "bold" && Font.boldMonospacedSystemFont)
    return Font.boldMonospacedSystemFont(size);
  if (Font.mediumMonospacedSystemFont) return Font.mediumMonospacedSystemFont(size);
  if (Font.regularMonospacedSystemFont) return Font.regularMonospacedSystemFont(size);
  return Font.systemFont(size);
}

// A signed amount, grouped in thousands. The minus is NEVER dropped: the
// pre-2026-10-22 widget printed Math.abs() with a "+" only for gains, so a
// $500 loss read "$500" and differed from a $500 gain by colour alone.
function money(v, dp) {
  if (v === undefined || v === null) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  const n = Math.abs(v).toFixed(dp === undefined ? 0 : dp);
  const [whole, frac] = n.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}$${grouped}${frac ? "." + frac : ""}`;
}


// How soon to ask iOS to refresh. This is a HINT, not a guarantee: Apple
// budgets a widget roughly 40-70 reloads per 24h and decides the real timing
// from battery, how often the widget is actually looked at, and system load.
// Scriptable's own docs: "It is not guaranteed that the widget will refresh at
// exactly the specified date."
//
// But the request still MATTERS — a shorter one measurably yields shorter
// intervals (community testing: a 30s request produced real refreshes every
// ~61s to ~3min). So a flat 15 minutes was leaving frequency on the table AND
// spending the budget badly: it spread ~96 requests evenly across 24 hours,
// landing only about a quarter of them inside the 6.5-hour session. The other
// 17.5 hours are provably static — the bot is flat, the numbers cannot move.
//
// So: concentrate the budget where the data actually changes.
function refreshInterval(marketOpen) {
  let hour = null, day = null;
  try {
    const f = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", hour: "numeric", hour12: false,
      weekday: "short" }).formatToParts(new Date());
    hour = Number(f.find((x) => x.type === "hour").value);
    day = f.find((x) => x.type === "weekday").value;
  } catch (e) { /* fall through to the safe default */ }

  if (marketOpen) return 2;                       // the session: as fast as iOS allows
  if (day === "Sat" || day === "Sun") return 60;  // nothing can change
  if (hour === null) return 15;                   // unknown clock: the old default
  if (hour >= 7 && hour < 10) return 10;          // pre-open: entries approach
  if (hour >= 16 && hour < 23) return 15;         // settlement lands ~21:45-22:37 ET
  return 60;                                      // overnight
}

// A fixed-width, right-aligned numeric cell. Without a fixed width the columns
// are laid out by their own text length, so "$0" drifts left as the figure
// beside it grows — which is exactly how the first fleet rendering looked.
function numCell(parent, text, width, color, size, weight) {
  const cell = parent.addStack();
  cell.size = new Size(width, 0);
  cell.layoutHorizontally();
  cell.addSpacer();
  const t = cell.addText(text);
  t.font = mono(size, weight);
  t.textColor = new Color(color);
  t.lineLimit = 1;
  return t;
}

// A 1pt hairline. The inner spacer is what makes the stack span the full
// width; a stack with no content collapses.
function rule(w, color) {
  const s = w.addStack();
  s.size = new Size(0, 1);
  s.backgroundColor = new Color(color || C.separator);
  s.addSpacer();
  return s;
}

function pnlColor(v) {
  if (v === undefined || v === null) return C.label3;
  return v < 0 ? C.red : v > 0 ? C.green : C.label2;
}

function row(w) {
  const s = w.addStack();
  s.layoutHorizontally();
  s.centerAlignContent();
  return s;
}

// `font` takes a Dynamic Type preset (T.caption2() etc). `size` remains for
// the few places that still need an explicit point size.
function label(stack, text, size, color, font) {
  const t = stack.addText(text);
  t.font = font || Font.systemFont(size);
  t.textColor = new Color(color);
  t.lineLimit = 1;
  return t;
}

function notice(w, title, detail) {
  const t = w.addText(title);
  t.font = Font.boldSystemFont(15);
  t.textColor = new Color(C.warn);
  w.addSpacer(4);
  const d = w.addText(detail);
  d.font = Font.systemFont(11);
  d.textColor = new Color(C.label2);
  d.minimumScaleFactor = 0.8;
}

// ── fleet view (Large / Medium) ────────────────────────────────────────────
//
// Grouped by COMPARABILITY, best-first within a group, and deliberately with
// NO global #1. Ranking all nine on one axis would be wrong four ways: a credit
// iron condor, a net-debit calendar and a naked strangle do not share a P&L
// axis; raw dollars rank by position SIZE (A is 1 contract at 75pt, bl is 7 at
// 5pt, H trades SPY at ~1/70th the notional); the lifetimes cover DIFFERENT
// eras (bl has 9 days of history against c's 94, which is why `days` is shown
// on every row); and 8 of the 9 are simulated.
function createFleetWidget(res, family, logo) {
  const w = new ListWidget();
  w.backgroundColor = new Color(C.card);
  // Apple specifies a 16pt margin from the widget edges (11pt only for
  // graphics-dense layouts). The first two versions used 12-13pt.
  w.setPadding(16, 16, 16, 16);
  w.url = DASHBOARD_URL;

  const d = res.data;
  if (!d) {
    if (res.problem === "no-token") notice(w, "Token needed", "Set WIDGET_TOKEN in this script.");
    else if (res.problem === "rejected") notice(w, "Token rejected", "The dashboard refused this token (401).");
    else notice(w, "Unreachable", `No response from ${DASHBOARD_URL.replace("https://", "")}.`);
    w.addSpacer();
    return w;
  }

  const today = etToday();
  const stale = res.source === "cache" || (today && d.date && d.date !== today);
  const compact = family === "medium";
  const groups = d.groups || [];
  const allRows = groups.reduce((a, g) => a.concat(g.rows || []), []);

  // A column of nine identical "$0" is data for its own sake — the single
  // most obvious thing wrong with the previous rendering. Show today's column
  // only once ANY strategy has actually traded; say so plainly otherwise.
  const anyActivity = allRows.some((r) => r.today);
  const W_LIFE = 78;
  const W_TODAY = 62;

  // ── title, with the mark ──
  const head = row(w);
  if (logo) {
    const li = head.addImage(logo);
    li.imageSize = new Size(20, 20);
    li.cornerRadius = 5;            // the mark is a square PNG with no alpha
    head.addSpacer(7);
  }
  const t = head.addText("HYDRA");
  t.font = T.headline();            // SF Pro. Monospace is for NUMERALS only.
  t.textColor = new Color(C.label);
  head.addSpacer();
  if (stale) {
    label(head, d.date ? `⚠ ${d.date}` : "⚠ stale", null, C.warn, T.caption2());
  } else {
    label(head, d.market_open ? "Open" : "Closed", null, C.label3, T.caption2());
  }

  // ── hero: the live seat ──
  if (d.live) {
    w.addSpacer(6);
    const sub = row(w);
    const tag = sub.addText("LIVE");
    tag.font = T.caption2();
    tag.textColor = new Color(C.blue);
    sub.addSpacer(5);
    label(sub, d.live.name, null, C.label2, T.caption2());

    // Before the first entry the live P&L is STRUCTURALLY zero, so a 22pt "$0"
    // was the largest and least informative element on the tile. When nothing
    // has traded yet, show the next scheduled entry instead — a time stays
    // true even on a stale tile, which a P&L figure does not.
    const hero = row(w);
    // Keyed on the LIVE SEAT, not the fleet. `anyActivity` is fleet-wide and
    // governs the TODAY column, which is correct — a shadow variant's P&L is
    // real information. But the hero is about the one strategy placing real
    // orders, and the two diverge every single morning: the shadow grids open
    // at 09:45 while the live seat's first slot is 12:15. Found live at 10:44
    // ET with B-A at -$32.20 and bl flat, where the fleet-wide flag showed a
    // 22pt "$0" and discarded the useful "12:15" — reintroducing exactly the
    // guaranteed-zero hero this logic exists to prevent.
    const liveTraded = !!(d.live && d.live.today);
    const showNext = !liveTraded && d.next_entry;
    const big = hero.addText(showNext ? d.next_entry : money(d.live.today));
    big.font = mono(N.hero, "bold");
    big.textColor = new Color(showNext ? C.label : pnlColor(d.live.today));
    hero.addSpacer();
    label(hero,
          showNext ? "next entry" : liveTraded ? "today" : "no entries today",
          null, C.label3, T.caption2());
  }

  w.addSpacer(9);

  // Column headings ONCE, in the SAME fixed-width cells as the figures they
  // describe. Putting them in each group header with a flexible spacer both
  // repeated them four times AND right-aligned them over the LIFETIME column,
  // so "TODAY" sat above the lifetime figures. Visible on the home screen.
  if (anyActivity) {
    const ch = row(w);
    ch.addSpacer();
    numCell(ch, "TODAY", W_TODAY, C.label3, N.head);
    numCell(ch, "LIFETIME", W_LIFE, C.label3, N.head);
    w.addSpacer(3);
  }

  groups.forEach((g, gi) => {
    if (gi > 0) { w.addSpacer(7); rule(w); w.addSpacer(7); }
    const gh = row(w);
    label(gh, (g.label || g.id), null, C.label3, T.caption2());
    gh.addSpacer();
    label(gh, g.pnl_shape === "debit" ? "debit" : "credit",
          null, C.label3, T.caption2());
    w.addSpacer(3);

    const rows = compact ? (g.rows || []).slice(0, 1) : (g.rows || []);
    rows.forEach((r, ri) => {
      if (ri > 0) w.addSpacer(3);
      const rr = row(w);
      const nm = rr.addText(r.name || r.id);
      nm.font = T.caption1();
      nm.textColor = new Color(r.live ? C.label : C.label2);
      nm.lineLimit = 1;
      nm.minimumScaleFactor = 0.9;
      if (r.days) {
        rr.addSpacer(5);
        label(rr, `${r.days}d`, null, C.label3, T.caption2());
      }
      rr.addSpacer();
      // The curve, before the figure: the shape says whether a lifetime number
      // is a steady bleed or one bad week, which the number alone cannot.
      const spark = sparkline(r.spark, 44, 14,
                              r.lifetime < 0 ? C.redDim : C.greenDim);
      if (spark) {
        const si = rr.addImage(spark);
        si.imageSize = new Size(44, 14);
        rr.addSpacer(8);
      }
      if (anyActivity) {
        numCell(rr, money(r.today), W_TODAY,
                r.today ? pnlColor(r.today) : C.label3, N.row);
      }
      numCell(rr, money(r.lifetime), W_LIFE,
              r.lifetime === null || r.lifetime === undefined
                ? C.label3 : pnlColor(r.lifetime), N.row);
    });
  });

  if (res.problem === "rejected") {
    w.addSpacer(6);
    label(w, "token rejected — cached figures", null, C.warn, T.caption2());
  }

  // Top-align: ListWidget CENTRES its content by default, which left the first
  // version with ~45% of the tile empty above and below a cramped block.
  w.addSpacer();

  const refresh = new Date();
  refresh.setMinutes(refresh.getMinutes() + refreshInterval(d.market_open));
  w.refreshAfterDate = refresh;
  return w;
}

function createWidget(res) {
  const w = new ListWidget();
  w.backgroundColor = new Color(C.card);
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
  title.textColor = new Color(C.green);

  // contracts badge — the backend exposes contracts_per_entry expressly for
  // this ("so iOS widget can show a [Nc] badge"); it went unused until now.
  const contracts = data.contracts_per_entry || 0;
  if (contracts > 1) {
    head.addSpacer(5);
    label(head, `${contracts}c`, 10, C.label3);
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
    dot.textColor = new Color(data.market_open ? C.green : C.label3);
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
  big.textColor = new Color(pnl < 0 ? C.red : pnl > 0 ? C.green : C.label2);
  big.minimumScaleFactor = 0.5;

  w.addSpacer(4);

  // ── entry dots ──
  const dots = row(w);
  dots.spacing = 4;
  const dotColors = {
    active: C.blue,
    expired: C.green,
    stopped: C.red,
    // A genuine order-execution FAILURE (2026-07-31) — same red as stopped so
    // it reads as "something went wrong", not as a routine non-event.
    failed: C.red,
    // SKIPPED was missing here and fell through to the pending grey, so six
    // skips at 15:00 looked like a day that had not started yet. The backend
    // separates the two deliberately (dashboard audit 2026-07-22) because
    // "expired" reads as a kept-credit win. Amber = a decision was made.
    skipped: C.warn,
    pending: C.label3,
  };
  (data.entry_dots || []).forEach((s) => {
    const d = dots.addText("●");
    d.font = Font.systemFont(10);
    d.textColor = new Color(dotColors[s] || C.label3);
  });

  w.addSpacer(4);

  // ── stats ──
  const stats = row(w);
  const n = data.entries || 0;
  const st = data.stops || 0;
  label(stats, `${n} ${n === 1 ? "entry" : "entries"}`, 10, C.label2);
  stats.addSpacer();
  label(stats, `${st} ${st === 1 ? "stop" : "stops"}`, 10,
        st > 0 ? C.red : C.label2);

  w.addSpacer(3);

  // ── lifetime ──
  // cumulative_pnl is rebased server-side precisely so this matches the web
  // dashboard's cumulative card; the two disagreed by ~$1,966 until the
  // 2026-07-22 fix, and the widget then ignored the result of that fix.
  const foot = row(w);
  const cum = data.cumulative_pnl;
  if (cum === undefined || cum === null) {
    label(foot, "lifetime —", 9, C.label3);
  } else {
    const cs = cum > 0 ? "+" : cum < 0 ? "−" : "";
    label(foot, `lifetime ${cs}$${Math.abs(cum).toFixed(0)}`, 9, C.label3);
  }
  foot.addSpacer();
  label(foot, stale ? "cached" : data.market_open ? "open" : "closed", 9, C.label3);

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
  refresh.setMinutes(refresh.getMinutes() + refreshInterval(data.market_open));
  w.refreshAfterDate = refresh;

  return w;
}

// Small  -> the live seat alone (today's P&L, entry dots) — the glanceable one.
// Medium -> each group's leader.
// Large  -> the whole fleet, grouped.
//
// Large is the biggest size iOS offers on iPhone; extraLarge exists but is
// iPad-only, so it falls through to the same fleet layout.
const family = config.widgetFamily || "large";

let widget;
let res;
if (family === "small") {
  res = await fetchData("/api/widget", CACHE_KEY);
  widget = createWidget(res);
} else {
  res = await fetchData("/api/widget/fleet", FLEET_CACHE_KEY);
  widget = createFleetWidget(res, family, await loadLogo());
}

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  // Run by hand inside Scriptable: say plainly what happened, because the
  // widget itself has only a few words of room for it.
  console.log(`family : ${family}`);
  console.log(`source : ${res.source || "none"}`);
  console.log(`problem: ${res.problem || "none"}`);
  console.log(`ET today: ${etToday()}  payload date: ${res.data ? res.data.date : "-"}`);
  if (res.data) console.log(JSON.stringify(res.data, null, 2));
  if (family === "small") widget.presentSmall();
  else if (family === "medium") widget.presentMedium();
  else widget.presentLarge();
}

Script.complete();
