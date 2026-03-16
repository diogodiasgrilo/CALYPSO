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
  actual_call_stop_debit: number;
  actual_put_stop_debit: number;
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
  call_long_value: number;
  put_long_value: number;
  call_stop_time: string;
  put_stop_time: string;
  skip_reason?: string;
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
  entry_schedule?: {
    base: string[];
    conditional: string[];
  };
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
  holiday_name?: string;
  early_close_reason?: string;
  is_fomc_day?: boolean;
  is_fomc_announcement?: boolean;
  is_fomc_t_plus_one?: boolean;
  next_fomc?: string;
  days_until_fomc?: number;
  next_event?: {
    next_open: string;
    hours_until_open: number;
  };
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

export interface PnLDataPoint {
  time: string; // HH:MM format
  pnl: number;
}

export interface StopEvent {
  entry_number: number;
  side: string;
  stop_time: string;
}

export interface AgentInfo {
  agent: string;
  last_run: string | null;
  last_file: string | null;
  available: boolean;
}

export interface ComparisonStats {
  avg_pnl: number;
  avg_entries: number;
  avg_stops: number;
  avg_credit: number;
  best_day: number;
  worst_day: number;
  total_days: number;
}

export interface Toast {
  id: string;
  type: "stop" | "entry" | "info" | "error";
  title: string;
  message: string;
  timestamp: number;
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

  // P&L history (server-side, from hydra_state.json pnl_history)
  pnlHistory: PnLDataPoint[];

  // Live log feed
  logLines: LogEntry[];

  // Live stop events (detected from state file transitions)
  stopEvents: StopEvent[];

  // Agent status (from WebSocket)
  agentStatus: AgentInfo[];

  // Comparison statistics (averages from historical data)
  comparisons: ComparisonStats | null;

  // Toast notifications
  toasts: Toast[];

  // UI preferences (persisted via actions, not localStorage — ephemeral per session)
  showStrikes: boolean;
  muted: boolean;

  // Actions
  setConnectionStatus: (status: ConnectionStatus) => void;
  applySnapshot: (data: Record<string, unknown>) => void;
  applyStateUpdate: (data: HydraState) => void;
  applyMetricsUpdate: (data: CumulativeMetrics) => void;
  applyMarketStatus: (data: MarketStatus) => void;
  applyOHLCUpdate: (data: OHLCBar[]) => void;
  applyLogLines: (lines: LogEntry[]) => void;
  applyStopEvents: (events: StopEvent[]) => void;
  applyAgentsUpdate: (agents: AgentInfo[]) => void;
  applyComparisons: (data: ComparisonStats) => void;
  addToast: (toast: Omit<Toast, "id" | "timestamp">) => void;
  removeToast: (id: string) => void;
  setClientCount: (count: number) => void;
  toggleStrikes: () => void;
  toggleMuted: () => void;
}

export const useHydraStore = create<DashboardStore>()(
  immer((set) => ({
    connectionStatus: "disconnected",
    clientCount: 0,
    hydraState: null,
    metrics: null,
    market: null,
    todayOHLC: [],
    pnlHistory: [],
    logLines: [],
    stopEvents: [],
    agentStatus: [],
    comparisons: null,
    toasts: [],
    showStrikes: false,
    muted: false,

    setConnectionStatus: (status) =>
      set((s) => {
        s.connectionStatus = status;
      }),

    applySnapshot: (data) =>
      set((s) => {
        if (data.state) {
          const state = data.state as HydraState;
          s.hydraState = state;
          // Use server-provided P&L history (persisted in hydra_state.json)
          const serverHistory = (state as Record<string, unknown>).pnl_history as PnLDataPoint[] | undefined;
          if (serverHistory && serverHistory.length > 0) {
            s.pnlHistory = serverHistory;
          }
        }
        if (data.metrics) s.metrics = data.metrics as CumulativeMetrics;
        if (data.market) s.market = data.market as MarketStatus;
        if (data.today_ohlc) s.todayOHLC = data.today_ohlc as OHLCBar[];
        if (data.today_stops) {
          const stops = data.today_stops as StopEvent[];
          s.stopEvents = stops.filter((e) => e.stop_time);
        }
        if (data.agents) s.agentStatus = data.agents as AgentInfo[];
        if (data.comparisons) s.comparisons = data.comparisons as ComparisonStats;
        if (data.clients) s.clientCount = data.clients as number;
      }),

    applyStateUpdate: (data) =>
      set((s) => {
        // Day boundary — reset stop events when date changes
        if (s.hydraState && s.hydraState.date !== data.date) {
          s.stopEvents = [];
        }
        s.hydraState = data;
        // Use server-provided P&L history (persisted in hydra_state.json by bot)
        const serverHistory = (data as Record<string, unknown>).pnl_history as PnLDataPoint[] | undefined;
        if (serverHistory && serverHistory.length > 0) {
          s.pnlHistory = serverHistory;
        }
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

    applyStopEvents: (events) =>
      set((s) => {
        // Merge and deduplicate by entry_number + side
        const existing = new Set(
          s.stopEvents.map((e) => `${e.entry_number}:${e.side}`)
        );
        for (const ev of events) {
          const key = `${ev.entry_number}:${ev.side}`;
          if (!existing.has(key)) {
            s.stopEvents.push(ev);
            existing.add(key);
          }
        }
      }),

    applyAgentsUpdate: (agents) =>
      set((s) => {
        s.agentStatus = agents;
      }),

    applyComparisons: (data) =>
      set((s) => {
        s.comparisons = data;
      }),

    addToast: (toast) =>
      set((s) => {
        s.toasts.push({
          ...toast,
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          timestamp: Date.now(),
        });
        // Keep at most 5 toasts
        if (s.toasts.length > 5) {
          s.toasts = s.toasts.slice(-5);
        }
      }),

    removeToast: (id) =>
      set((s) => {
        s.toasts = s.toasts.filter((t) => t.id !== id);
      }),

    setClientCount: (count) =>
      set((s) => {
        s.clientCount = count;
      }),

    toggleStrikes: () =>
      set((s) => {
        s.showStrikes = !s.showStrikes;
      }),

    toggleMuted: () =>
      set((s) => {
        s.muted = !s.muted;
      }),
  }))
);
