/** HYDRA brand color system — extracted from the HYDRA logo. */

export const colors = {
  // Backgrounds (matched to HYDRA logo)
  bg: "#2a3a42",
  bgDeep: "#1e2c33",
  bgElevated: "#344a52",
  border: "#3d5058",
  borderDim: "#2f424a",

  // P&L
  profit: "#7ee8c7",
  profitMuted: "#5a9e8a",
  loss: "#f85149",
  lossMuted: "#c9413a",
  warning: "#d29922",
  warningMuted: "#a67b1a",
  info: "#58a6ff",

  // Text
  textPrimary: "#e8edf3",
  textSecondary: "#8b9bb0",
  textDim: "#5e6e82",

  // Cards
  card: "#1e2c33",
  cardHover: "#253540",
} as const;

/** Color for a P&L value. */
export function pnlColor(value: number): string {
  if (value > 0) return colors.profit;
  if (value < 0) return colors.loss;
  return colors.textSecondary;
}

/** VIX level color coding. */
export function vixColor(vix: number): string {
  if (vix < 15) return colors.profit;
  if (vix < 20) return colors.warning;
  if (vix < 25) return "#f0883e"; // orange
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
  status: "active" | "expired" | "stopped" | "stopped_single" | "skipped" | "pending" | "placing"
): string {
  switch (status) {
    case "active":
      return colors.info;
    case "placing":
      return colors.warning;
    case "expired":
      return colors.profit;
    case "stopped":
      return colors.loss; // double stop = red
    case "stopped_single":
      return colors.warning; // single stop = amber/yellow
    case "skipped":
      return colors.textDim;
    case "pending":
      return colors.textDim;
  }
}
