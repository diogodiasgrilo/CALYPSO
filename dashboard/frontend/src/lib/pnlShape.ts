/**
 * P&L-shape config (audit AUD-3-F5). A group's comparison axis is determined
 * entirely by its `pnl_shape` (credit vs debit). Every group chart/table takes
 * the shape as a HARD input; a chart that receives a mismatched shape must
 * render an error card rather than silently mix two axes.
 *
 * - credit  = net-credit 0DTE iron condors (HYDRA/Brandon). Capital basis is
 *   spread-width notional; P&L is credit captured minus close debit.
 * - debit   = net-debit multi-day calendars (DC Time Machine / SPY DC). Capital
 *   basis is the net debit paid; there is NO credit/buffer/spread-width.
 *
 * This replaces the positional VARIANT_ACCENTS map with a deterministic,
 * id-derived accent so the same letter always gets the same color regardless
 * of which group/page renders it. (Accents are a fallback; if the backend ever
 * provides per-strategy accents we read those instead — none today.)
 */

import { colors } from "./tradingColors";
import type { PnlShape } from "../hooks/useStrategyMeta";

export interface PnlShapeConfig {
  /** Y-axis label for P&L charts. */
  axisLabel: string;
  /** What the "capital" basis is called for this shape. */
  capitalLabel: string;
  /** The zero reference is always $0; this is the human description. */
  zeroRef: string;
  /** Short human name for the structure family. */
  structureName: string;
  /** Whether credit/buffer/spread fields are meaningful for this shape. */
  hasCreditFields: boolean;
}

export const PNL_SHAPES: Record<"credit" | "debit", PnlShapeConfig> = {
  credit: {
    axisLabel: "Net P&L ($)",
    capitalLabel: "Capital deployed (width notional)",
    zeroRef: "$0 (breakeven)",
    structureName: "Iron Condor",
    hasCreditFields: true,
  },
  debit: {
    axisLabel: "Realized P&L ($)",
    capitalLabel: "Capital at risk (net debit)",
    zeroRef: "$0 (breakeven)",
    structureName: "Double Calendar",
    hasCreditFields: false,
  },
};

/**
 * Per-CAPITAL-BASIS labelling (dashboard rebuild Phase 4, 2026-09-17).
 *
 * PNL_SHAPES above answers "how is P&L earned". This answers "what is the
 * denominator", and the two are independent: a naked strangle and an iron
 * condor are both `credit` shaped, but one has a spread width and the other has
 * a broker margin requirement. Labelling a strangle's capital "width notional"
 * — which PNL_SHAPES.credit.capitalLabel does — is simply false.
 *
 * `boundedLoss:false` is the one that must never be got wrong: for an
 * undefined-risk position, printing any "max loss" figure is a lie, so the card
 * shows UNBOUNDED instead of a number.
 */
export interface CapitalBasisConfig {
  /** Label for the average-capital card. */
  capitalLabel: string;
  /** Label for the return-on-capital card. */
  returnLabel: string;
  /** Human description of the denominator, for tooltips. */
  denominator: string;
  /** False = loss is structurally unbounded; never render a max-loss number. */
  boundedLoss: boolean;
}

export const CAPITAL_BASES: Record<
  "defined_risk" | "net_debit" | "broker_margin",
  CapitalBasisConfig
> = {
  defined_risk: {
    capitalLabel: "Capital / Day",
    returnLabel: "ROI (on capital)",
    denominator: "spread width x 100 x contracts",
    boundedLoss: true,
  },
  net_debit: {
    capitalLabel: "Debit at Risk / Day",
    returnLabel: "Return on Debit",
    denominator: "net debit paid",
    boundedLoss: true,
  },
  broker_margin: {
    capitalLabel: "Peak Margin / Day",
    returnLabel: "Return on Margin",
    denominator: "broker margin requirement (undefined risk — no structural cap)",
    boundedLoss: false,
  },
};

/** Config for a known capital basis, or null when unknown. */
export function capitalBasisConfig(basis: string | undefined): CapitalBasisConfig | null {
  if (basis === "defined_risk" || basis === "net_debit" || basis === "broker_margin") {
    return CAPITAL_BASES[basis];
  }
  return null;
}

/** Config for a known shape, or null for "unknown" (caller renders an error). */
export function pnlShapeConfig(shape: PnlShape): PnlShapeConfig | null {
  if (shape === "credit" || shape === "debit") return PNL_SHAPES[shape];
  return null;
}

// ── Accents ─────────────────────────────────────────────────────────────────
// Deterministic per-letter accent (replaces positional VARIANT_ACCENTS). Keyed
// by the lowercase letter so A is always blue, C always mint, etc. — stable no
// matter which group renders it. Letters past the palette fall back to a hashed
// hue so a 6th+ variant still gets a distinct, stable color.
const ACCENT_PALETTE: Record<string, string> = {
  a: colors.info, // blue
  b: colors.warning, // amber
  c: colors.profit, // mint
  d: colors.loss, // coral
  e: "#a371f7", // purple
};

function hashedHue(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360}, 60%, 62%)`;
}

/** Stable accent color for a strategy/variant id (case-insensitive). */
export function accentForStrategy(id: string): string {
  const key = (id || "").trim().toLowerCase();
  return ACCENT_PALETTE[key] ?? (key ? hashedHue(key) : colors.textPrimary);
}
