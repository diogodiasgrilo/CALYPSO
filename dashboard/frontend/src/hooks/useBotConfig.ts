/**
 * Bot config flags for the SELECTED strategy.
 *
 * ⚠️ IT USED TO FETCH THE PRIMARY'S CONFIG FOR EVERY STRATEGY, and the symptom
 * was visible on screen: with variant H picked — a strategy with exactly ONE
 * entry at 09:45 — the Timeline rendered "09:45 … 12:45", which is variant B's
 * seven-slot grid. The same was true of A, C, F and G; H only made it obvious.
 *
 * Two independent causes, both fixed here:
 *   1. `fetch("/api/hydra/bot-config")` passed NO `strategy_id`, and the
 *      backend falls back to `live_config_file()` when none is given.
 *   2. The cache was a single module-level `BotConfig`, so even with an id the
 *      first strategy fetched would have stuck for every later one.
 *
 * The cache is now keyed by strategy id, which is what makes switching in the
 * picker actually refetch.
 */

import { useEffect, useState } from "react";
import { useSelectedStrategy } from "./useSelectedStrategy";

interface BotConfig {
  conditional_e6_enabled: boolean;
  conditional_e7_enabled: boolean;
  /** Downday-035 (2026-04-19): conditional E6 call-only on down days. */
  conditional_downday_e6_enabled: boolean;
  conditional_downday_e7_enabled: boolean;
  conditional_downday_threshold_pct: number;
  conditional_upday_e6_enabled: boolean;
  conditional_upday_e7_enabled: boolean;
  downday_threshold_pct: number;
  upday_threshold_pct: number;
  /** Canonical base entry times (pre-VIX-cap). E1 = entry_times[0], E2 = entry_times[1], … */
  entry_times: string[];
  /** Canonical conditional entry times. E{entry_times.length + 1 + i}. */
  conditional_entry_times: string[];
  /** 2026-04-27: dry_run flag for prominent dashboard banner. True when the
   *  PRIMARY bot is in dry mode (real IBKR-paper prices, no real orders). */
  dry_run?: boolean;
  /** 2026-06-02: human label for which strategy the main page is showing
   *  (e.g. "C · LIVE (Brandon narrow 5/10pt)"). Shown in the header. */
  primary_label?: string;
}

const DEFAULT_CONFIG: BotConfig = {
  conditional_e6_enabled: false,
  conditional_e7_enabled: false,
  conditional_downday_e6_enabled: false,
  conditional_downday_e7_enabled: false,
  conditional_downday_threshold_pct: 0.0025,
  conditional_upday_e6_enabled: false,
  conditional_upday_e7_enabled: false,
  downday_threshold_pct: 0.003,
  upday_threshold_pct: 0.0025,
  entry_times: [],
  conditional_entry_times: [],
  dry_run: false,
  primary_label: "",
};

/** Per-strategy cache. A single shared slot was half the bug this hook had. */
const _cache = new Map<string, BotConfig>();

/** Returns the selected strategy's bot config (cached per strategy id). */
export function useBotConfig(): BotConfig {
  const { strategy } = useSelectedStrategy();
  // "" means "whatever the backend considers canonical", which is the correct
  // request while meta is still loading — it is the historical behaviour and
  // the only sensible default before a selection exists.
  const id = strategy?.id ?? "";
  const [cfg, setCfg] = useState<BotConfig>(_cache.get(id) ?? DEFAULT_CONFIG);

  useEffect(() => {
    const hit = _cache.get(id);
    if (hit) {
      setCfg(hit);
      return;
    }
    let cancelled = false;
    const qs = id ? `?strategy_id=${encodeURIComponent(id)}` : "";
    fetch(`/api/hydra/bot-config${qs}`)
      .then((r) => r.json())
      .then((data: BotConfig) => {
        _cache.set(id, data);
        if (!cancelled) setCfg(data);
      })
      .catch(() => {
        // On error, keep defaults (show nothing hidden unintentionally)
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return cfg;
}

/** Returns true if any conditional entry slot (downday OR upday) is enabled. */
export function useShowConditionalEntries(): boolean {
  const cfg = useBotConfig();
  return (
    cfg.conditional_e6_enabled ||
    cfg.conditional_e7_enabled ||
    cfg.conditional_downday_e6_enabled ||
    cfg.conditional_downday_e7_enabled ||
    cfg.conditional_upday_e6_enabled ||
    cfg.conditional_upday_e7_enabled
  );
}
