/** Shared types for the History page and its sub-components. */

export interface DaySummary {
  date: string;
  net_pnl: number;
  gross_pnl: number;
  entries_placed: number;
  entries_stopped: number;
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
