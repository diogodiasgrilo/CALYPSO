/**
 * Iron-condor main-dashboard view. Two data sources, one layout intent:
 *
 *   - source === "ws"  → the PRIMARY strategy. Renders the EXISTING live-
 *     WebSocket component tree EXACTLY as the old Dashboard.tsx did — zero
 *     regression. These widgets read the WS store directly.
 *
 *   - source === polled snapshot body → a NON-primary IC strategy. Renders the
 *     same IC information (day summary, entries with per-side cushions, intraday
 *     P&L) built from the polled /api/strategies/{id}/snapshot body. The heavy
 *     WS-only widgets (live SPX TradingView chart, agents, log feed) are
 *     process-global, not per-strategy, so they stay on the primary view only.
 */

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
} from "recharts";

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
import { useHydraStore } from "../../store/hydraStore";
import { colors, pnlColor } from "../../lib/tradingColors";
import { formatPnL, isRegularSessionTime } from "../../lib/formatters";
import { ICEntryRow, BufferBar } from "./icEntryView";
import type { ICSnapshotBody } from "../../hooks/useStrategySnapshot";

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

// ── NON-PRIMARY (polled) IC view — built from the snapshot body ──
function StatCell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="text-center">
      <div className="text-[10px] text-text-dim uppercase tracking-wider mb-0.5">{label}</div>
      <div className="text-sm font-semibold text-text-primary">{children}</div>
    </div>
  );
}

function PolledDaySummary({ body }: { body: ICSnapshotBody }) {
  const s = body.summary;
  const netPnl = s?.net_pnl ?? 0;
  const credit = s?.total_credit_received ?? 0;
  const commission = s?.total_commission ?? 0;
  const stops = s?.total_stops ?? 0;
  const entriesDone = s?.entries_completed ?? 0;

  return (
    <div className="bg-card rounded-lg border border-border-dim p-4">
      <h3 className="label-upper mb-2">Today</h3>
      <div className="text-center mb-4">
        <span className="metric-hero" style={{ color: pnlColor(netPnl) }}>
          {formatPnL(netPnl)}
        </span>
      </div>
      <div className="grid grid-cols-4 gap-1 pt-3 border-t border-border-dim">
        <StatCell label="Entries">{entriesDone}</StatCell>
        <StatCell label="Stops">
          <span style={{ color: stops > 0 ? colors.loss : colors.textPrimary }}>{stops}</span>
        </StatCell>
        <StatCell label="Credit">${credit.toFixed(0)}</StatCell>
        <StatCell label="Comm.">${commission.toFixed(0)}</StatCell>
      </div>
    </div>
  );
}

function PolledPnLChart({ body }: { body: ICSnapshotBody }) {
  const series = (body.pnl_history ?? []).filter((p) => isRegularSessionTime(p.time));
  if (series.length === 0) {
    return (
      <div className="rounded border border-border-dim bg-card p-4">
        <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">P&amp;L Over Time</div>
        <div className="text-sm text-text-dim italic">
          No P&amp;L data yet — appears once an entry has been monitored for one heartbeat cycle.
        </div>
      </div>
    );
  }
  return (
    <div className="rounded border border-border-dim bg-card p-3">
      <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">P&amp;L Over Time</div>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={series} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={colors.borderDim} strokeDasharray="3 3" />
          <XAxis dataKey="time" stroke={colors.textSecondary} tick={{ fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis stroke={colors.textSecondary} tick={{ fontSize: 10 }} tickFormatter={(v) => `$${v}`} />
          <Tooltip
            contentStyle={{ backgroundColor: colors.bgElevated, border: `1px solid ${colors.border}`, borderRadius: 4, fontSize: 12 }}
            formatter={(value) => [formatPnL(Number(value)), "Net P&L"]}
          />
          <ReferenceLine y={0} stroke={colors.border} />
          <Line type="monotone" dataKey="pnl" stroke={colors.info} strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function PolledICView({ body, accent }: { body: ICSnapshotBody; accent: string }) {
  if (!body.available) {
    return (
      <div className="p-6 text-text-secondary">
        <h2 className="text-lg text-text-primary mb-2">Strategy not running</h2>
        <p className="text-sm">{body.reason ?? "This strategy's state file is not present yet."}</p>
      </div>
    );
  }

  const entries = body.entries ?? [];
  const margin = body.min_buffer_margin ?? { call_pct: 100, put_pct: 100 };
  const spx = body.spx_price;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-12 gap-3 max-lg:grid-cols-1">
        <div className="col-span-8 max-lg:col-span-1">
          <PolledPnLChart body={body} />
        </div>
        <div className="col-span-4 max-lg:col-span-1">
          <PolledDaySummary body={body} />
        </div>
      </div>

      <div className="rounded border border-border-dim bg-card p-4 space-y-2">
        <div className="text-xs uppercase tracking-wide text-text-secondary">Entries</div>
        {entries.length === 0 ? (
          <div className="text-sm text-text-dim italic">No entries yet today.</div>
        ) : (
          entries.map((e, i) => <ICEntryRow key={i} entry={e} accent={accent} spx={spx} />)
        )}
        <div className="border-t border-border-dim pt-2">
          <div className="text-xs text-text-secondary mb-1">
            Worst cushion today{" "}
            <span className="text-text-dim">(low-water — not live · 100% safe → 0% at stop)</span>
          </div>
          <div className="flex gap-4 text-xs">
            <BufferBar label="Call" pct={margin.call_pct} muted />
            <BufferBar label="Put" pct={margin.put_pct} muted />
          </div>
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
