/** Zustand store for all dashboard state (fed by WebSocket). */

import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

// ── Types ──

export interface HydraEntry {
  entry_number: number;
  entry_time: string | null;
  short_call_strike: number;
  long_call_strike: number;
  short_put_strike: number;
  long_put_strike: number;
  call_spread_credit: number;
  put_spread_credit: number;
  call_side_stop: number;
  put_side_stop: number;
  short_call_fill_price: number;
  long_call_fill_price: number;
  short_put_fill_price: number;
  long_put_fill_price: number;
  is_complete: boolean;
  call_side_stopped: boolean;
  put_side_stopped: boolean;
  call_side_expired: boolean;
  put_side_expired: boolean;
  call_side_skipped: boolean;
  put_side_skipped: boolean;
  call_only: boolean;
  put_only: boolean;
  trend_signal: string | null;
  override_reason: string | null;
  open_commission: number;
  close_commission: number;
  contracts: number;
  call_long_sold: boolean;
  put_long_sold: boolean;
  call_long_sold_revenue: number;
  put_long_sold_revenue: number;
  call_spread_value: number;
  put_spread_value: number;
  [key: string]: unknown;
}

export interface HydraState {
  date: string;
  state: string;
  entries: HydraEntry[];
  entries_completed: number;
  entries_failed: number;
  entries_skipped: number;
  total_credit_received: number;
  total_realized_pnl: number;
  total_commission: number;
  call_stops_triggered: number;
  put_stops_triggered: number;
  one_sided_entries: number;
  credit_gate_skips: number;
  market_data_ohlc?: {
    spx_open: number;
    spx_high: number;
    spx_low: number;
    vix_open: number;
    vix_high: number;
    vix_low: number;
  };
  last_saved: string;
  [key: string]: unknown;
}

export interface CumulativeMetrics {
  cumulative_pnl: number;
  total_trades: number;
  total_entries: number;
  winning_days: number;
  losing_days: number;
  total_credit_collected: number;
  total_stops: number;
  double_stops: number;
  last_updated: string;
}

export interface MarketStatus {
  session: string;
  is_open: boolean;
  is_trading_day: boolean;
  is_early_close: boolean;
}

export interface OHLCBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  vix?: number;
}

export interface LogEntry {
  timestamp: string;
  level: string;
  component: string;
  message: string;
}

export type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

// ── Store ──

interface DashboardStore {
  // Connection
  connectionStatus: ConnectionStatus;
  clientCount: number;

  // HYDRA state (from hydra_state.json)
  hydraState: HydraState | null;

  // Cumulative metrics (from hydra_metrics.json)
  metrics: CumulativeMetrics | null;

  // Market status
  market: MarketStatus | null;

  // Chart data
  todayOHLC: OHLCBar[];

  // Live log feed
  logLines: LogEntry[];

  // Actions
  setConnectionStatus: (status: ConnectionStatus) => void;
  applySnapshot: (data: Record<string, unknown>) => void;
  applyStateUpdate: (data: HydraState) => void;
  applyMetricsUpdate: (data: CumulativeMetrics) => void;
  applyMarketStatus: (data: MarketStatus) => void;
  applyOHLCUpdate: (data: OHLCBar[]) => void;
  applyLogLines: (lines: LogEntry[]) => void;
  setClientCount: (count: number) => void;
}

export const useHydraStore = create<DashboardStore>()(
  immer((set) => ({
    connectionStatus: "disconnected",
    clientCount: 0,
    hydraState: null,
    metrics: null,
    market: null,
    todayOHLC: [],
    logLines: [],

    setConnectionStatus: (status) =>
      set((s) => {
        s.connectionStatus = status;
      }),

    applySnapshot: (data) =>
      set((s) => {
        if (data.state) s.hydraState = data.state as HydraState;
        if (data.metrics) s.metrics = data.metrics as CumulativeMetrics;
        if (data.market) s.market = data.market as MarketStatus;
        if (data.today_ohlc) s.todayOHLC = data.today_ohlc as OHLCBar[];
        if (data.clients) s.clientCount = data.clients as number;
      }),

    applyStateUpdate: (data) =>
      set((s) => {
        s.hydraState = data;
      }),

    applyMetricsUpdate: (data) =>
      set((s) => {
        s.metrics = data;
      }),

    applyMarketStatus: (data) =>
      set((s) => {
        s.market = data;
      }),

    applyOHLCUpdate: (data) =>
      set((s) => {
        s.todayOHLC = data;
      }),

    applyLogLines: (lines) =>
      set((s) => {
        s.logLines = [...s.logLines, ...lines].slice(-500); // Keep last 500 lines
      }),

    setClientCount: (count) =>
      set((s) => {
        s.clientCount = count;
      }),
  }))
);
