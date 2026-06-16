/**
 * Resolves the effective main-dashboard strategy selection.
 *
 * The raw `selectedStrategyId` in the store is a localStorage UI pref that can
 * be null (never chosen) or stale (a letter removed from the taxonomy). This
 * hook validates it against the live meta and returns the EFFECTIVE selection:
 *
 *   - meta still loading → resolves to null (callers show the WS/primary view).
 *   - selection unset / not in meta.byId / not main_dashboard-capable
 *       → falls back to the taxonomy primary.
 *   - otherwise → the selected StrategyInfo.
 *
 * `isPrimarySelected` lets the dashboard keep the zero-regression live-WS
 * fast-path when the primary IC strategy is the active selection.
 */

import { useMemo } from "react";
import { useHydraStore } from "../store/hydraStore";
import { useStrategyMeta } from "./useStrategyMeta";
import type { StrategyInfo } from "./useStrategyMeta";

export interface SelectedStrategy {
  /** The effective strategy (validated). null while meta loads / none registered. */
  strategy: StrategyInfo | null;
  /** True when the effective selection is the taxonomy primary. */
  isPrimarySelected: boolean;
  /** True while meta is still loading. */
  loading: boolean;
}

export function useSelectedStrategy(): SelectedStrategy {
  const meta = useStrategyMeta();
  const selectedId = useHydraStore((s) => s.selectedStrategyId);

  return useMemo(() => {
    if (meta.loading) {
      return { strategy: null, isPrimarySelected: true, loading: true };
    }
    const primary = meta.byId[meta.primaryId] ?? null;

    let chosen: StrategyInfo | null = null;
    if (selectedId) {
      const candidate = meta.byId[selectedId];
      // Only honor a selection that can actually drive the main dashboard.
      if (candidate && candidate.capabilities.main_dashboard) chosen = candidate;
    }
    const strategy = chosen ?? primary;
    const isPrimarySelected = !!strategy && strategy.id === meta.primaryId;
    return { strategy, isPrimarySelected, loading: false };
  }, [meta, selectedId]);
}
