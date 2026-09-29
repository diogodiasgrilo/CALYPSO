/** Shared types for the History page and its sub-components. */

export interface DaySummary {
  date: string;
  net_pnl: number;
  gross_pnl: number;
  /** P&L belonging to the DAY but to no entry — the round-trip price P&L of a
   *  failed entry attempt's unwound legs (ORDER-010). It is real money and
   *  correctly inside `net_pnl`, but it is NOT a trading result.
   *
   *  2026-09-24 is why this is surfaced: +$5,995 of it against a −$2,663
   *  trading day. The headline read +$3,331.55 and the calendar painted it the
   *  second-best day of the month. The column has always been served (the
   *  reader does `SELECT *`); nothing read it. */
  unattributed_overlay_pnl?: number;
  entries_placed: number;
  entries_stopped: number;
  /** Authoritative stop-loss event count (trade_stops). entries_stopped
   *  conflates Brandon take-profit exits for B/C; prefer this for "Stops". */
  actual_stops?: number;
  entries_expired: number;
  commission: number;
  spx_open: number;
  spx_close: number;
  vix_open: number;
  day_type: string;
  day_of_week: string;
}

export interface DayEntry {
  entry_number: number;
  entry_time: string;
  spx_at_entry: number;
  vix_at_entry: number;
  trend_signal: string;
  entry_type: string;
  override_reason: string;
  short_call_strike: number;
  long_call_strike: number;
  short_put_strike: number;
  long_put_strike: number;
  call_credit: number;
  put_credit: number;
  total_credit: number;
  otm_distance_call: number;
  otm_distance_put: number;
}

export interface DayStop {
  entry_number: number;
  side: string;
  stop_time: string;
  spx_at_stop: number;
  trigger_level: number;
  actual_debit: number;
  net_pnl: number;
  salvage_sold: number;
  salvage_revenue: number;
  // v11: distinguishes a genuine credit+buffer "stop_loss" from a managed close
  // (MKT-047 "early_close" EOD flatten, Brandon "take_profit" / "gex_breach"),
  // all of which write a trade_stops row. Absent on legacy pre-v11 rows.
  exit_reason?: string;
}

export interface OHLCBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

export type SortKey =
  | "date"
  | "net_pnl"
  | "entries_placed"
  | "entries_stopped"
  | "spx_close"
  | "vix_open";

export type SortDir = "asc" | "desc";
