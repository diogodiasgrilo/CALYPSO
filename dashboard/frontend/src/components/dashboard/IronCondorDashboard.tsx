/**
 * Iron-condor main-dashboard view. Two data sources, ONE layout intent:
 *
 *   - source === "ws"  → the PRIMARY strategy. Renders the EXISTING live-
 *     WebSocket component tree EXACTLY as the old Dashboard.tsx did — zero
 *     regression. These widgets read the WS store directly (no props passed).
 *
 *   - source === polled snapshot body → a NON-primary IC strategy. Renders the
 *     SAME panels as the primary (SPX candle chart with the variant's own entry
 *     markers, the Today + Cumulative P&L cards, the intraday P&L curve, the
 *     entry grid, the position heatmap, the Performance metrics, the entry
 *     timeline) — but feeds each widget the polled /api/strategies/{id}/snapshot
 *     body via props. The widgets fall back to the WS store when NOT given a
 *     prop, so the primary path is byte-identical.
 *
 * Process-global widgets: AgentStatusPanel is rendered on both (the 5-agent
 * suite is shared, not per-strategy). LiveLogFeed is per-PROCESS (the dashboard
 * tails the primary's log only), so it renders on the primary and shows a small
 * "log lives on the variant's own process" note off-primary — parity of the
 * trading panels is NOT gated on the log.
 */

import { SPXChart } from "../market/SPXChart";
import { DailyPnLCard } from "../pnl/DailyPnLCard";
import { PnLCurve } from "../pnl/PnLCurve";
import { EntryGrid } from "../entries/EntryGrid";
import { EntryTimeline } from "../entries/EntryTimeline";
import { AgentStatusPanel } from "../agents/AgentStatusPanel";
import { LiveLogFeed } from "../logs/LiveLogFeed";
import { PerformanceMetrics } from "../pnl/PerformanceMetrics";
import { PositionHeatmap } from "../market/PositionHeatmap";
import {
  MarketContextBanner,
  FOMCBanner,
  OffDaySummaryCards,
} from "../market/MarketContextBanner";
import { useHydraStore, type HydraEntry, type OHLCBar } from "../../store/hydraStore";
import { BufferBar } from "./icEntryView";
import type {
  ICSnapshotBody,
  ICSnapshotEntry,
  ICSnapshotOHLCBar,
} from "../../hooks/useStrategySnapshot";

// ── PRIMARY (WebSocket) view — the original Dashboard layout, unchanged ──
function PrimaryICView() {
  const realizedPnl = useHydraStore((s) => s.hydraState?.total_realized_pnl ?? 0);
  const commission = useHydraStore((s) => s.hydraState?.total_commission ?? 0);
  const market = useHydraStore((s) => s.market);
  const hydraState = useHydraStore((s) => s.hydraState);
  const todayOHLC = useHydraStore((s) => s.todayOHLC);
  const netPnl = realizedPnl - commission;

  const entries = hydraState?.entries;
  const isLive = market?.is_open === true;
  const hasEntries = entries?.some((e) => e.entry_time) ?? false;
  const hasChartData = todayOHLC.length > 0;
  const showFullLayout = isLive || hasEntries || hasChartData;

  const ambientClass =
    netPnl > 0 ? "ambient-profit" : netPnl < 0 ? "ambient-loss" : "";

  return (
    <div className="relative">
      {showFullLayout && ambientClass && (
        <div
          className={`absolute inset-x-0 top-0 h-64 pointer-events-none ${ambientClass}`}
        />
      )}

      <div className="relative space-y-3">
        <FOMCBanner />

        {showFullLayout ? (
          <>
            <div className="grid grid-cols-12 gap-3 max-lg:grid-cols-1">
              <div className="col-span-8 max-lg:col-span-1">
                <SPXChart />
              </div>
              <div className="col-span-4 max-lg:col-span-1">
                <DailyPnLCard />
              </div>
            </div>
            <PnLCurve />
            <EntryGrid />
            <PositionHeatmap />
            <PerformanceMetrics />
            <EntryTimeline />
          </>
        ) : (
          <>
            <MarketContextBanner />
            <OffDaySummaryCards />
            <PerformanceMetrics />
          </>
        )}

        <AgentStatusPanel />
        <LiveLogFeed />
      </div>
    </div>
  );
}

// ── Snapshot → widget adapters ──────────────────────────────────────────────
// The widgets consume the WS store's HydraEntry / OHLCBar shapes. The polled
// snapshot's ICSnapshotEntry / ICSnapshotOHLCBar carry the SAME field names but
// with looser (optional) typing; these coercions fill the required numeric/bool
// fields with safe defaults so a widget reads identical values whether the row
// came from the WS store or the snapshot. No behavior change — just typing.

function coerceEntry(e: ICSnapshotEntry): HydraEntry {
  const n = (v: unknown): number => (typeof v === "number" ? v : 0);
  const b = (v: unknown): boolean => v === true;
  const s = (v: unknown): string => (typeof v === "string" ? v : "");
  return {
    ...e,
    entry_number: n(e.entry_number),
    entry_time: (e.entry_time as string | null) ?? null,
    short_call_strike: n(e.short_call_strike),
    long_call_strike: n(e.long_call_strike),
    short_put_strike: n(e.short_put_strike),
    long_put_strike: n(e.long_put_strike),
    call_spread_credit: n(e.call_spread_credit),
    put_spread_credit: n(e.put_spread_credit),
    call_side_stop: n(e.call_side_stop),
    put_side_stop: n(e.put_side_stop),
    effective_call_stop: n(e.effective_call_stop),
    effective_put_stop: n(e.effective_put_stop),
    actual_call_stop_debit: n(e.actual_call_stop_debit),
    actual_put_stop_debit: n(e.actual_put_stop_debit),
    short_call_fill_price: n(e.short_call_fill_price),
    long_call_fill_price: n(e.long_call_fill_price),
    short_put_fill_price: n(e.short_put_fill_price),
    long_put_fill_price: n(e.long_put_fill_price),
    is_complete: b(e.is_complete),
    call_side_stopped: b(e.call_side_stopped),
    put_side_stopped: b(e.put_side_stopped),
    call_side_expired: b(e.call_side_expired),
    put_side_expired: b(e.put_side_expired),
    call_side_skipped: b(e.call_side_skipped),
    put_side_skipped: b(e.put_side_skipped),
    close_reason: (e.close_reason as string | null | undefined) ?? null,
    call_only: b(e.call_only),
    put_only: b(e.put_only),
    trend_signal: (e.trend_signal as string | null | undefined) ?? null,
    override_reason: (e.override_reason as string | null | undefined) ?? null,
    open_commission: n(e.open_commission),
    close_commission: n(e.close_commission),
    contracts: n(e.contracts) || 1,
    call_long_sold: b(e.call_long_sold),
    put_long_sold: b(e.put_long_sold),
    call_long_sold_revenue: n(e.call_long_sold_revenue),
    put_long_sold_revenue: n(e.put_long_sold_revenue),
    call_spread_value: n(e.call_spread_value),
    put_spread_value: n(e.put_spread_value),
    call_long_value: n(e.call_long_value),
    put_long_value: n(e.put_long_value),
    call_stop_time: s(e.call_stop_time),
    put_stop_time: s(e.put_stop_time),
  } as HydraEntry;
}

function coerceOHLC(bars: ICSnapshotOHLCBar[]): OHLCBar[] {
  return bars.map((b) => ({
    timestamp: b.timestamp,
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
    vix: b.vix,
  }));
}

// ── NON-PRIMARY (polled) IC view — full parity with the primary panels ──
function PolledICView({ body }: { body: ICSnapshotBody; accent: string }) {
  if (!body.available) {
    return (
      <div className="p-6 text-text-secondary">
        <h2 className="text-lg text-text-primary mb-2">Strategy not running</h2>
        <p className="text-sm">{body.reason ?? "This strategy's state file is not present yet."}</p>
      </div>
    );
  }

  const snapEntries = body.entries ?? [];
  const entries = snapEntries.map(coerceEntry);
  const ohlc = coerceOHLC(body.ohlc ?? []);
  const spx = body.spx_price ?? 0;
  const margin = body.min_buffer_margin ?? { call_pct: 100, put_pct: 100 };
  const netPnl = body.summary?.net_pnl ?? 0;

  const ambientClass =
    netPnl > 0 ? "ambient-profit" : netPnl < 0 ? "ambient-loss" : "";

  return (
    <div className="relative">
      {ambientClass && (
        <div
          className={`absolute inset-x-0 top-0 h-64 pointer-events-none ${ambientClass}`}
        />
      )}

      <div className="relative space-y-3">
        {/* SPX candle chart (this variant's OWN bars) + Today/Cumulative cards */}
        <div className="grid grid-cols-12 gap-3 max-lg:grid-cols-1">
          <div className="col-span-8 max-lg:col-span-1">
            <SPXChart ohlc={ohlc} entries={entries} date={body.date ?? null} />
          </div>
          <div className="col-span-4 max-lg:col-span-1">
            <DailyPnLCard summary={body.summary} cumulative={body.cumulative ?? {}} />
          </div>
        </div>

        <PnLCurve pnlHistory={body.pnl_history ?? []} />
        <EntryGrid entries={entries} />
        <PositionHeatmap entries={entries} spx={spx} />
        <PerformanceMetrics dailyPnls={body.performance?.daily_pnls ?? []} />
        <EntryTimeline entries={entries} />

        {/* Worst-cushion (low-water) footnote — preserved muted semantics. This
            is the historical low-water mark, distinct from the live per-entry
            cushions the EntryGrid / PositionHeatmap show. */}
        <div className="rounded border border-border-dim bg-card p-4 space-y-2">
          <div className="text-xs text-text-secondary">
            Worst cushion today{" "}
            <span className="text-text-dim">(low-water — not live · 100% safe → 0% at stop)</span>
          </div>
          <div className="flex gap-4 text-xs">
            <BufferBar label="Call" pct={margin.call_pct} muted />
            <BufferBar label="Put" pct={margin.put_pct} muted />
          </div>
        </div>

        {/* Agents are process-global (the 5-agent suite is shared) — render. */}
        <AgentStatusPanel />

        {/* The live log is per-PROCESS (the dashboard tails the primary's log
            only), so we don't show the primary's log under a non-primary
            selection. A short note keeps the operator oriented. */}
        <div className="text-[11px] text-text-dim italic px-1">
          Live log streams on this variant's own process — view it via its journal
          (e.g. <span className="font-mono not-italic">journalctl -u hydra_variant_*</span>).
        </div>
      </div>
    </div>
  );
}

interface IronCondorDashboardProps {
  source: "ws" | { body: ICSnapshotBody; accent: string };
}

export function IronCondorDashboard({ source }: IronCondorDashboardProps) {
  if (source === "ws") return <PrimaryICView />;
  return <PolledICView body={source.body} accent={source.accent} />;
}
