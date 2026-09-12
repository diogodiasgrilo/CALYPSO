/**
 * Tiny shared store for the currently-selected NON-primary strategy snapshot.
 *
 * StrategyDashboard owns the single /api/strategies/{id}/snapshot poll and
 * publishes the latest envelope here; the Header subscribes to re-bind its
 * chrome (label/dry-run banner/underlying/SPX/VIX) to the SELECTED strategy
 * without owning a second poll (audit AUD-3-F1).
 *
 * `snapshot === null` means "viewing the primary" — the Header then falls back
 * to the live WebSocket chrome exactly as before (zero regression).
 */

import { create } from "zustand";
import type { StrategySnapshot } from "../../hooks/useStrategySnapshot";

interface SelectedSnapshotStore {
  snapshot: StrategySnapshot | null;
  setSnapshot: (s: StrategySnapshot | null) => void;
}

export const useSelectedSnapshotStore = create<SelectedSnapshotStore>((set) => ({
  snapshot: null,
  setSnapshot: (s) => set({ snapshot: s }),
}));
