import { SPXChart } from "../components/market/SPXChart";
import { DailyPnLCard } from "../components/pnl/DailyPnLCard";
import { PnLCurve } from "../components/pnl/PnLCurve";
import { EntryGrid } from "../components/entries/EntryGrid";
import { EntryTimeline } from "../components/entries/EntryTimeline";
import { AgentStatusPanel } from "../components/agents/AgentStatusPanel";
import { LiveLogFeed } from "../components/logs/LiveLogFeed";

export function Dashboard() {
  return (
    <div className="space-y-3">
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

      {/* Timeline */}
      <EntryTimeline />

      {/* Agents */}
      <AgentStatusPanel />

      {/* Live log */}
      <LiveLogFeed />
    </div>
  );
}
