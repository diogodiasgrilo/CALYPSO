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

/** A strategy's data renderer kind — drives StrategyDashboard's dispatch.
 *
 * `long_gamma` added 2026-09-23. `data_kind` used to be a two-way switch
 * ("double calendar, else iron condor"), which silently drew variant H — net
 * DEBIT, no credit, no spread width — as an iron condor. The kinds now follow
 * `pnl_shape`, which is the property the renderers actually disagree about. */
export type DataKind = "ic_state" | "dc_calendar" | "long_gamma";

/** Credit (net-credit IC) vs debit (net-debit calendar). Forbids cross-shape charts. */
export type PnlShape = "credit" | "debit" | "unknown";

/**
 * What CAPITAL means for a strategy — a different question from `pnl_shape`,
 * which is how P&L is EARNED. Conflating them is what made the naked strangle
 * render as an iron condor: it sells premium (pnl_shape "credit", same as the
 * ICs) but has no spread width for a return to be a percentage OF, so
 * return-on-capital was UNDEFINED rather than merely missing.
 */
export type CapitalBasis = "defined_risk" | "net_debit" | "broker_margin" | "unknown";

/** Whether a strategy ever places BOTH a call and a put side. */
export type Sides = "one_sided" | "two_sided" | "unknown";

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
  /** Dashboard label. The NAME says which one; `subtitle` says what it is,
   *  and never both. Distinct from display_name, which is the live seat's
   *  alert identity and must not change for a UI tidy-up. */
  ui_name?: string;
  /** One-line spec rendered under the name in the switcher (taxonomy, 2026-09-18). */
  subtitle?: string;
  short_name?: string;
  group_id: string;
  family: string; // structure_family: "iron_condor" | "double_calendar" | "unknown"
  pnl_shape: PnlShape;
  capital_basis: CapitalBasis;
  sides: Sides;
  /** Holding horizon — "0DTE" | "multi_day" | "unknown". A chart bucketed by
   *  day-of-week or entry-slot is meaningless for a multi-day position. */
  dte_class: string;
  /** WHAT causes an entry attempt — "clock" (fixed times) or "event" (a market
   *  condition). An event-triggered strategy has NO schedule to render, and
   *  inferring one from an empty `entry_times` is what gave variant F a
   *  fabricated 10:15/10:45/11:15 grid. */
  schedule_kind?: string;
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
      // eslint-disable-next-line react-hooks/set-state-in-effect -- the module cache can be populated between this hook's useState initialiser (render) and this effect (commit). Without the sync, a component mounting inside that window shows EMPTY_META forever, because it was not subscribed yet either
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
