/** Fetches bot config flags from the backend (read once on mount). */

import { useEffect, useState } from "react";

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
};

let _cachedConfig: BotConfig | null = null;

/** Returns the full bot config (cached after first fetch). */
export function useBotConfig(): BotConfig {
  const [cfg, setCfg] = useState<BotConfig>(_cachedConfig ?? DEFAULT_CONFIG);

  useEffect(() => {
    if (_cachedConfig) return;
    fetch("/api/hydra/bot-config")
      .then((r) => r.json())
      .then((data: BotConfig) => {
        _cachedConfig = data;
        setCfg(data);
      })
      .catch(() => {
        // On error, keep defaults (show nothing hidden unintentionally)
      });
  }, []);

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
