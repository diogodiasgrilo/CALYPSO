/** HYDRA brand color system — 3-level surface depth + semantic colors.
 *
 * MUST STAY IN STEP WITH `index.css`. These values are duplicated there as
 * `--color-*` custom properties: Tailwind classes and plain CSS read the custom
 * properties, while the charting libraries (recharts, lightweight-charts) need
 * concrete strings and read THIS object. Two copies of one palette is a drift
 * hazard, and the drift had already happened when this note was written —
 * `bgDeep` here said `#1a2229` (a copy of `bg`, labelled "alias for bg") while
 * `--color-bg-deep` in CSS was `#161d23`. It had no visual effect only because
 * nothing consumed `colors.bgDeep`; the first caller would have got the wrong
 * shade.
 *
 * `tests/test_dashboard_palette_parity_2026_09_17.py` parses both files and
 * fails on any mismatch. A runtime read of the custom properties was considered
 * and rejected: `getComputedStyle` at module load can run before the stylesheet
 * applies, and a chart silently painted in fallback colours is worse than a
 * build-time failure. */

export const colors = {
  // Surface depth system (3 elevation levels)
  bg: "#1a2229", // Level 0 — page background
  bgDeep: "#161d23", // Level -1 — deepest recess (tab bars, modals)
  card: "#222e35", // Level 1 — cards/panels
  cardHover: "#1d282e", // Level 1 hover
  bgElevated: "#2d3b43", // Level 2 — modals/tooltips/dropdowns
  border: "#3d5058",
  borderDim: "rgba(255, 255, 255, 0.06)",

  // P&L
  profit: "#7ee8c7",
  profitMuted: "#5ca18d",
  loss: "#f95f58",
  lossMuted: "#cd5049",
  warning: "#d29922",
  warningMuted: "#a67b1a",
  info: "#5da8ff",

  // Text
  textPrimary: "#e8edf3",
  textSecondary: "#c2cad5",
  textDim: "#98a5b5",
} as const;

/**
 * Type-scale steps as NUMBERS, for charting libraries (recharts,
 * lightweight-charts) whose props take a px number and cannot use a CSS class.
 *
 * Mirrors `--text-*` in index.css. Without this a chart label is a magic number
 * that silently escapes the scale — `EquityCurve`'s marker label sat at 9px
 * after 9px had been retired everywhere else, and only a rendered font-size
 * census caught it.
 */
export const fontSizePx = {
  /** 10px — labels, badges, dense tabular data. Matches `text-3xs`. */
  xs3: 10,
  /** 11px — smallest readable prose. Matches `text-2xs`. */
  xs2: 11,
  /** 12px — Tailwind `text-xs`. */
  xs: 12,
} as const;

/** Color for a P&L value. */
export function pnlColor(value: number): string {
  if (value > 0) return colors.profit;
  if (value < 0) return colors.loss;
  return colors.textSecondary;
}

/** VIX level color coding. Aligns with HYDRA VIX regime breakpoints [18, 22, 28]. */
export function vixColor(vix: number): string {
  if (vix < 18) return colors.profit;
  if (vix < 22) return colors.warning;
  if (vix < 28) return "#f0883e"; // orange
  return colors.loss;
}

/** Cushion percentage → gradient color (green→amber→red). */
export function cushionColor(pct: number): string {
  if (pct >= 60) return colors.profit;
  if (pct >= 40) return colors.profitMuted;
  if (pct >= 25) return colors.warning;
  if (pct >= 15) return "#f0883e";
  return colors.loss;
}

/** Entry status badge colors. */
export function statusColor(
  status: "active" | "expired" | "flattened" | "stopped" | "stopped_single" | "skipped" | "failed" | "pending" | "placing" | "take_profit" | "breach"
): string {
  switch (status) {
    case "active":
      return colors.info;
    case "placing":
      return colors.warning;
    case "expired":
      return colors.profit;
    case "flattened":
      return colors.info; // EOD safety flatten = a managed close, not a stop/expiry
    case "take_profit":
      return colors.profit; // Brandon TP = profitable close = green (NOT a stop)
    case "breach":
      return colors.warning; // Brandon GEX-breach exit = defensive close = amber
    case "stopped":
      return colors.loss; // double stop = red
    case "stopped_single":
      return colors.warning; // single stop = amber/yellow
    case "skipped":
      return colors.textDim;
    case "failed":
      // Broker/execution failure, not a strategic choice — same severity color
      // as a double stop (colors.loss) so it visually stands apart from the
      // neutral gray "skipped" badge.
      return colors.loss;
    case "pending":
      return colors.textDim;
  }
}
