/**
 * Polls GET /api/strategies/{id}/snapshot for a NON-primary strategy selection
 * (the primary keeps the live-WebSocket fast-path — see StrategyDashboard).
 *
 * The 2s cadence matches the Comparison page's proven poll. The envelope carries
 * the full header chrome (display_name/label/dry_run/underlying_symbol) so the
 * Header can re-bind to the selection (audit AUD-3-F1), plus a `data_kind` that
 * tells the dispatcher which body renderer to use. A missing state file →
 * `body.available === false` (never a 500).
 *
 * Pass `enabled=false` (e.g. while viewing the primary) to suspend polling.
 */

import { useEffect, useState } from "react";
import type { DataKind, PnlShape } from "./useStrategyMeta";

const POLL_MS = 2000;

// ── IC (ic_state) body — mirrors /api/variants per-variant payload ──
export interface ICSnapshotEntry {
  entry_number?: number;
  entry_time?: string | null;
  short_call_strike?: number;
  long_call_strike?: number;
  short_put_strike?: number;
  long_put_strike?: number;
  call_spread_credit?: number;
  put_spread_credit?: number;
  total_credit?: number;
  call_side_stop?: number;
  put_side_stop?: number;
  effective_call_stop?: number;
  effective_put_stop?: number;
  call_side_stopped?: boolean;
  put_side_stopped?: boolean;
  call_side_expired?: boolean;
  put_side_expired?: boolean;
  call_side_skipped?: boolean;
  put_side_skipped?: boolean;
  is_complete?: boolean;
  disposition?: string;
  close_reason?: string;
  skip_reason?: string;
  call_side_pivot_closed?: boolean;
  put_side_pivot_closed?: boolean;
  actual_call_stop_debit?: number;
  actual_put_stop_debit?: number;
  call_stop_time?: string;
  put_stop_time?: string;
  buffer?: {
    call_pct: number | null;
    put_pct: number | null;
    call_value: number | null;
    put_value: number | null;
  };
  [key: string]: unknown;
}

export interface ICSnapshotSummary {
  date?: string;
  state?: string;
  entries_completed: number;
  entries_failed: number;
  entries_skipped: number;
  total_credit_received: number;
  total_realized_pnl: number;
  total_commission: number;
  net_pnl: number;
  call_stops: number;
  put_stops: number;
  total_stops: number;
  active_entries: number;
  total_entries: number;
}

export interface PnLPoint {
  time: string;
  pnl: number;
}

// 1-min SPX OHLC bar — matches the WS store's OHLCBar + market_ohlc_1min row
// shape so the SPXChart candle path is byte-identical whether the bars come
// from the WS store or this polled snapshot.
export interface ICSnapshotOHLCBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  vix?: number;
}

// Lifetime cumulative metrics for DailyPnLCard's "Cumulative" section. Same
// shape as /api/metrics/cumulative (the DB-canonical rollup), per-variant.
export interface ICSnapshotCumulative {
  cumulative_pnl?: number;
  winning_days?: number;
  losing_days?: number;
  total_credit_collected?: number;
  total_entries?: number;
  total_stops?: number;
  cumulative_baseline_date?: string;
  capital_deployed?: number;
  avg_capital_per_day?: number;
  roi_pct?: number;
  [key: string]: unknown;
}

// Advanced-stats payload for PerformanceMetrics — `daily_pnls` is the same array
// /api/metrics/performance returns (the widget computes the ratios client-side).
export interface ICSnapshotPerformance {
  daily_pnls?: number[];
  advanced?: {
    sharpe: number;
    max_drawdown: number;
    best_day: number;
    worst_day: number;
  };
}

export interface ICSnapshotBody {
  available: boolean;
  reason?: string;
  state_file_age_seconds?: number;
  summary?: ICSnapshotSummary;
  entries?: ICSnapshotEntry[];
  pnl_history?: PnLPoint[];
  min_buffer_margin?: { call_pct: number; put_pct: number };
  spx_price?: number;
  spx_open?: number;
  spx_high?: number;
  spx_low?: number;
  vix_open?: number;
  state?: string;
  date?: string;
  // Full-mirror enrichment (2026-06): the variant's OWN today OHLC bars,
  // lifetime cumulative metrics, and performance stats so a non-primary IC view
  // renders the SAME panels as the primary WS view.
  ohlc?: ICSnapshotOHLCBar[];
  cumulative?: ICSnapshotCumulative;
  performance?: ICSnapshotPerformance;
}

// ── Calendar (dc_calendar) body — debit-native, NO IC fields ──
export interface DCOpenCalendar {
  entry_number: number | null;
  strategy_id: string | null;
  dc_phase: string | null;
  is_risk_free: boolean;
  contracts: number | null;
  net_debit: number | null;
  transform_credit: number | null;
  /** Live mark-to-market P&L (dollars); null on pre-2026-06-17 sidecars. */
  unrealized_pnl: number | null;
  /** Live P&L as a percent of net debit; null if unavailable. */
  pnl_pct: number | null;
  call_strike: number | null;
  put_strike: number | null;
  short_expiry: string | null;
  long_expiry: string | null;
}

export interface DCRecentOutcome {
  entry_date: string;
  close_date: string;
  entry_number: number;
  terminal_state: string;
  realized_pnl: number;
}

export interface DCCalendarSummary {
  open_count: number;
  transformed_count: number;
  risk_free_count: number;
  realized_pnl_recent: number;
}

export interface DCSnapshotBody {
  available: boolean;
  reason?: string;
  open_calendars?: DCOpenCalendar[];
  recent_outcomes?: DCRecentOutcome[];
  calendar_summary?: DCCalendarSummary;
}

export type SnapshotBody = ICSnapshotBody | DCSnapshotBody;

export interface StrategySnapshot {
  id: string;
  data_kind: DataKind;
  group_id: string;
  pnl_shape: PnlShape;
  is_primary: boolean;
  display_name: string;
  label: string | null;
  dry_run: boolean | null;
  underlying_symbol: string | null;
  body: SnapshotBody;
}

interface SnapshotResult {
  snapshot: StrategySnapshot | null;
  loading: boolean;
  error: string | null;
}

export function useStrategySnapshot(
  strategyId: string | null,
  enabled: boolean = true,
): SnapshotResult {
  const [snapshot, setSnapshot] = useState<StrategySnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !strategyId) {
      setSnapshot(null);
      setLoading(false);
      return;
    }
    let mounted = true;
    setLoading(true);
    // Reset stale snapshot on id change so the body never shows the wrong
    // strategy for a frame.
    setSnapshot((prev) => (prev && prev.id === strategyId ? prev : null));

    const tick = async () => {
      try {
        const r = await fetch(`/api/strategies/${strategyId}/snapshot`);
        if (!mounted) return;
        if (!r.ok) {
          setError(`HTTP ${r.status}`);
          setLoading(false);
          return;
        }
        const json = (await r.json()) as StrategySnapshot;
        if (!mounted) return;
        // Guard against a late response for a previous id.
        if (json.id !== strategyId) return;
        setSnapshot(json);
        setError(null);
        setLoading(false);
      } catch {
        if (!mounted) return;
        setError("Network error");
        setLoading(false);
      }
    };

    tick();
    const handle = setInterval(tick, POLL_MS);
    return () => {
      mounted = false;
      clearInterval(handle);
    };
  }, [strategyId, enabled]);

  return { snapshot, loading, error };
}
