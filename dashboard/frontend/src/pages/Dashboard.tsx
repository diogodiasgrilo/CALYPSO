/**
 * Main dashboard page — now a thin wrapper around StrategyDashboard, which
 * dispatches by the SELECTED strategy's data_kind (ic_state vs dc_calendar) and
 * source (live WebSocket fast-path for the primary IC, polled snapshot for any
 * other strategy). The picker + header re-binding live in the layout/header.
 *
 * See dashboard/frontend/src/components/dashboard/StrategyDashboard.tsx and
 * docs/STRATEGY_GROUPING_REDESIGN.md §2a.
 */

import { StrategyDashboard } from "../components/dashboard/StrategyDashboard";

export function Dashboard() {
  return <StrategyDashboard />;
}
