/**
 * Per-strategy main view dispatcher (docs/STRATEGY_GROUPING_REDESIGN.md §2a).
 *
 * Dispatch:
 *   1. selected === primary AND ic_state → the EXISTING live-WebSocket fast-path
 *      (IronCondorDashboard source="ws") — zero regression, the default view.
 *   2. non-primary ic_state → poll /api/strategies/{id}/snapshot (2s) and render
 *      the IC layout from the polled body.
 *   3. dc_calendar → poll the snapshot and render the calendar view.
 *
 * The Header re-binds to the SELECTED strategy via the StrategyHeaderBinder
 * (audit AUD-3-F1) — see Header.tsx. This component owns the single snapshot
 * poll for non-primary selections and publishes it so the Header reads the same
 * source (avoids a second poll).
 */

import { useSelectedStrategy } from "../../hooks/useSelectedStrategy";
import { useStrategySnapshot } from "../../hooks/useStrategySnapshot";
import { IronCondorDashboard } from "./IronCondorDashboard";
import { CalendarDashboard } from "./CalendarDashboard";
import { accentForStrategy } from "../../lib/pnlShape";
import { useSelectedSnapshotStore } from "./selectedSnapshotStore";
import { useEffect } from "react";
import type {
  ICSnapshotBody,
  DCSnapshotBody,
} from "../../hooks/useStrategySnapshot";

export function StrategyDashboard() {
  const { strategy, isPrimarySelected, loading } = useSelectedStrategy();
  const publishSnapshot = useSelectedSnapshotStore((s) => s.setSnapshot);

  // Poll only for a non-primary selection; the primary uses the live WS store.
  const pollEnabled = !loading && !!strategy && !isPrimarySelected;
  const { snapshot } = useStrategySnapshot(
    pollEnabled ? strategy!.id : null,
    pollEnabled,
  );

  // Publish the polled snapshot (or null when on the primary) so the Header can
  // re-bind to the selection without owning its own poll. Cleared on the
  // primary so the Header falls back to the WS chrome.
  useEffect(() => {
    if (!pollEnabled) {
      publishSnapshot(null);
      return;
    }
    publishSnapshot(snapshot);
  }, [pollEnabled, snapshot, publishSnapshot]);

  if (loading || !strategy) {
    // Meta not loaded yet — show the live WS primary view (the historical
    // default, zero regression for the common case).
    return <IronCondorDashboard source="ws" />;
  }

  // 1. Primary IC → live WebSocket fast-path.
  if (isPrimarySelected && strategy.data_kind === "ic_state") {
    return <IronCondorDashboard source="ws" />;
  }

  const accent = accentForStrategy(strategy.id);

  // 3. Calendar strategy → polled calendar view.
  if (strategy.data_kind === "dc_calendar") {
    if (!snapshot) {
      return <div className="p-6 text-text-secondary">Loading {strategy.display_name}…</div>;
    }
    return (
      <CalendarDashboard
        body={snapshot.body as DCSnapshotBody}
        displayName={snapshot.display_name}
        dryRun={snapshot.dry_run}
      />
    );
  }

  // 2. Non-primary IC → polled IC view.
  if (!snapshot) {
    return <div className="p-6 text-text-secondary">Loading {strategy.display_name}…</div>;
  }
  return (
    <IronCondorDashboard
      source={{ body: snapshot.body as ICSnapshotBody, accent }}
    />
  );
}
