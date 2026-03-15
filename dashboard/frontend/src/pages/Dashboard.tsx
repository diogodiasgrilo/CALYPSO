import { SPXChart } from "../components/market/SPXChart";
import { DailyPnLCard } from "../components/pnl/DailyPnLCard";
import { PnLCurve } from "../components/pnl/PnLCurve";
import { EntryGrid } from "../components/entries/EntryGrid";
import { EntryTimeline } from "../components/entries/EntryTimeline";
import { AgentStatusPanel } from "../components/agents/AgentStatusPanel";
import { LiveLogFeed } from "../components/logs/LiveLogFeed";
import { PerformanceMetrics } from "../components/pnl/PerformanceMetrics";
import { PositionHeatmap } from "../components/market/PositionHeatmap";
import { useHydraStore } from "../store/hydraStore";

export function Dashboard() {
  const realizedPnl = useHydraStore((s) => s.hydraState?.total_realized_pnl ?? 0);
  const commission = useHydraStore((s) => s.hydraState?.total_commission ?? 0);
  const netPnl = realizedPnl - commission;

  const ambientClass =
    netPnl > 0 ? "ambient-profit" : netPnl < 0 ? "ambient-loss" : "";

  return (
    <div className="relative">
      {/* Ambient state gradient overlay */}
      {ambientClass && (
        <div
          className={`absolute inset-x-0 top-0 h-64 pointer-events-none ${ambientClass}`}
        />
      )}

      <div className="relative space-y-3">
        {/* Top row: Chart + P&L sidebar */}
        <div className="grid grid-cols-12 gap-3 max-lg:grid-cols-1">
          <div className="col-span-8 max-lg:col-span-1">
            <SPXChart />
          </div>
          <div className="col-span-4 max-lg:col-span-1">
            <DailyPnLCard />
          </div>
        </div>

        {/* P&L Curve */}
        <PnLCurve />

        {/* Entry cards */}
        <EntryGrid />

        {/* Position Heatmap */}
        <PositionHeatmap />

        {/* Performance Metrics */}
        <PerformanceMetrics />

        {/* Timeline */}
        <EntryTimeline />

        {/* Agents */}
        <AgentStatusPanel />

        {/* Live log */}
        <LiveLogFeed />
      </div>
    </div>
  );
}
