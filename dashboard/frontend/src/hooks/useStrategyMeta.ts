/**
 * Strategy taxonomy boot payload — fetched ONCE from /api/strategies/meta and
 * module-cached (mirrors useBotConfig.ts's caching). This is the single source
 * of truth the data-driven frontend reads from; it REPLACES the ad-hoc
 * useComparisonEnabled / useDcEnabled probes in App.tsx.
 *
 * NO hardcoded variant letters anywhere downstream — groups, members, the
 * primary id, per-strategy capabilities, and the comparison axis (pnl_shape)
 * all come from this payload. See docs/STRATEGY_GROUPING_REDESIGN.md §2a.
 */

import { useEffect, useState } from "react";

/** A strategy's data renderer kind — drives StrategyDashboard's dispatch. */
export type DataKind = "ic_state" | "dc_calendar";

/** Credit (net-credit IC) vs debit (net-debit calendar). Forbids cross-shape charts. */
export type PnlShape = "credit" | "debit" | "unknown";

export interface StrategyCapabilities {
  /** Can be the main-dashboard picker's selection. */
  main_dashboard: boolean;
  /** Member of a *comparable* group (shows in group tabs). */
  comparison: boolean;
  /** History page (today: IC-only — primary-bound readers). */
  history: boolean;
  /** Analytics page (today: IC-only — primary-bound readers). */
  analytics: boolean;
  /** DC-native open-calendars / outcomes view. */
  calendar_cards: boolean;
}

export interface StrategyInfo {
  id: string; // lowercase letter key ("a".."e")
  display_name: string;
  group_id: string;
  family: string; // structure_family: "iron_condor" | "double_calendar" | "unknown"
  pnl_shape: PnlShape;
  data_kind: DataKind;
  is_live: boolean;
  is_primary: boolean;
  available: boolean; // the variant's state file exists (its bot has run)
  capabilities: StrategyCapabilities;
}

export interface GroupInfo {
  id: string;
  label: string;
  pnl_shape: PnlShape;
  comparable: boolean;
  member_ids: string[]; // lowercase letters
  baseline_id: string | null; // lowercase letter the group's deltas are measured against
}

export interface StrategyMetaPayload {
  primary_id: string;
  groups: GroupInfo[];
  strategies: StrategyInfo[];
}

export interface StrategyMeta {
  groups: GroupInfo[];
  strategies: StrategyInfo[];
  primaryId: string;
  /** Strategies keyed by lowercase id, for O(1) lookup + boot validation. */
  byId: Record<string, StrategyInfo>;
  /** Groups keyed by group id. */
  groupById: Record<string, GroupInfo>;
  loading: boolean;
}

const EMPTY_META: StrategyMeta = {
  groups: [],
  strategies: [],
  primaryId: "",
  byId: {},
  groupById: {},
  loading: true,
};

let _cached: StrategyMeta | null = null;
/** Subscribers waiting for the first fetch (so concurrent hooks all update). */
const _subscribers = new Set<(m: StrategyMeta) => void>();
let _inFlight = false;

function buildMeta(p: StrategyMetaPayload): StrategyMeta {
  const byId: Record<string, StrategyInfo> = {};
  for (const s of p.strategies) byId[s.id] = s;
  const groupById: Record<string, GroupInfo> = {};
  for (const g of p.groups) groupById[g.id] = g;
  return {
    groups: p.groups,
    strategies: p.strategies,
    primaryId: p.primary_id,
    byId,
    groupById,
    loading: false,
  };
}

function fetchMeta(): void {
  if (_inFlight || _cached) return;
  _inFlight = true;
  fetch("/api/strategies/meta")
    .then((r) => (r.ok ? r.json() : null))
    .then((data: StrategyMetaPayload | null) => {
      if (!data || !Array.isArray(data.strategies)) {
        // Backend missing the endpoint (older deploy) — leave an empty,
        // not-loading sentinel so the picker simply renders nothing rather
        // than spinning forever.
        _cached = { ...EMPTY_META, loading: false };
      } else {
        _cached = buildMeta(data);
      }
    })
    .catch(() => {
      _cached = { ...EMPTY_META, loading: false };
    })
    .finally(() => {
      _inFlight = false;
      const snapshot = _cached ?? { ...EMPTY_META, loading: false };
      for (const cb of _subscribers) cb(snapshot);
    });
}

/** Returns the cached strategy taxonomy. Fetches once on first use. */
export function useStrategyMeta(): StrategyMeta {
  const [meta, setMeta] = useState<StrategyMeta>(_cached ?? EMPTY_META);

  useEffect(() => {
    if (_cached) {
      setMeta(_cached);
      return;
    }
    _subscribers.add(setMeta);
    fetchMeta();
    return () => {
      _subscribers.delete(setMeta);
    };
  }, []);

  return meta;
}
