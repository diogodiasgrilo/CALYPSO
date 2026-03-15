import { SPXChart } from "../components/market/SPXChart";
import { DailyPnLCard } from "../components/pnl/DailyPnLCard";
import { PnLCurve } from "../components/pnl/PnLCurve";
import { EntryGrid } from "../components/entries/EntryGrid";
import { EntryTimeline } from "../components/entries/EntryTimeline";
import { AgentStatusPanel } from "../components/agents/AgentStatusPanel";
import { LiveLogFeed } from "../components/logs/LiveLogFeed";
import { PerformanceMetrics } from "../components/pnl/PerformanceMetrics";
import { PositionHeatmap } from "../components/market/PositionHeatmap";
import { MarketContextBanner, FOMCBanner } from "../components/market/MarketContextBanner";
import { useHydraStore } from "../store/hydraStore";

export function Dashboard() {
  const realizedPnl = useHydraStore((s) => s.hydraState?.total_realized_pnl ?? 0);
  const commission = useHydraStore((s) => s.hydraState?.total_commission ?? 0);
  const market = useHydraStore((s) => s.market);
  const entries = useHydraStore((s) => s.hydraState?.entries ?? []);
  const todayOHLC = useHydraStore((s) => s.todayOHLC);
  const netPnl = realizedPnl - commission;

  const isLive = market?.is_open === true;
  const hasEntries = entries.some((e) => e.entry_time);
  const hasChartData = todayOHLC.length > 0;
  const showFullLayout = isLive || hasEntries || hasChartData;

  const ambientClass =
    netPnl > 0 ? "ambient-profit" : netPnl < 0 ? "ambient-loss" : "";

  return (
    <div className="relative">
      {/* Ambient state gradient overlay — only during active trading */}
      {showFullLayout && ambientClass && (
        <div
          className={`absolute inset-x-0 top-0 h-64 pointer-events-none ${ambientClass}`}
        />
      )}

      <div className="relative space-y-3">
        {/* FOMC overlay — shown in any layout */}
        <FOMCBanner />

        {showFullLayout ? (
          <>
            {/* Full trading layout */}
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
            {/* Compact layout — weekend, holiday, pre-market */}
            <MarketContextBanner />
            <DailyPnLCard />
            <PerformanceMetrics />
          </>
        )}

        {/* Always shown */}
        <AgentStatusPanel />
        <LiveLogFeed />
      </div>
    </div>
  );
}
