/** Fetches bot config flags from the backend (read once on mount). */

import { useEffect, useState } from "react";

interface BotConfig {
  conditional_e6_enabled: boolean;
  conditional_e7_enabled: boolean;
}

/** Returns true if at least one of E6/E7 conditional entries is enabled in bot config. */
export function useShowConditionalEntries(): boolean {
  const [show, setShow] = useState(true);

  useEffect(() => {
    fetch("/api/hydra/bot-config")
      .then((r) => r.json())
      .then((cfg: BotConfig) => {
        setShow(cfg.conditional_e6_enabled === true || cfg.conditional_e7_enabled === true);
      })
      .catch(() => {
        // On error, keep showing (safe default — hides nothing unintentionally)
      });
  }, []);

  return show;
}
