import { AlertOctagon } from "lucide-react";
import { useHydraStore } from "../../store/hydraStore";
import { colors } from "../../lib/tradingColors";

/** DATA-002 — entries halted because a feed we trade on is not real-time.
 *
 * WHY THIS EXISTS. From 2026-11-02 the whole fleet silently received SPX and
 * VIX delayed by 15-20 minutes (IBKR field 6509 = "D") and kept trading on it
 * for five sessions. The bot logged a warning ~20,000 times a day, stamped the
 * stale price as fresh, and the dashboard showed nothing at all — so the only
 * visible symptom was a live seat that had quietly stopped completing entries.
 *
 * The point of this strip is that "the bot isn't trading" and "the bot cannot
 * safely trade, for this reason, since this time" look completely different.
 *
 * Rendered ABOVE the FOMC strip: a degraded feed outranks a calendar policy,
 * because it means every price on the page is suspect.
 */
export function DataFeedBanner() {
  const hydraState = useHydraStore((s) => s.hydraState);

  if (!hydraState?.market_data_degraded) return null;

  const reason = hydraState.market_data_reason ?? "A market-data feed is not real-time.";
  const since = hydraState.market_data_degraded_since;

  return (
    <div
      className="rounded-lg border p-6"
      style={{
        backgroundColor: colors.card,
        // Full-strength loss red, not the dim border: this is the one state
        // where the operator must not be able to scroll past the strip.
        borderColor: colors.loss,
      }}
      role="alert"
      aria-live="assertive"
    >
      <div className="flex items-center gap-3 mb-4">
        <AlertOctagon size={20} style={{ color: colors.loss }} />
        <span
          className="text-xs font-bold uppercase tracking-widest"
          style={{ color: colors.loss }}
        >
          Trading halted · market data not real-time
        </span>
      </div>

      <p className="text-sm text-text-secondary mb-4">{reason}</p>

      <div className="grid grid-cols-3 gap-4 max-sm:grid-cols-1">
        <div>
          <div className="label-upper mb-1">New positions</div>
          <div className="metric-body font-bold" style={{ color: colors.loss }}>
            BLOCKED
          </div>
        </div>
        <div>
          <div className="label-upper mb-1">Exits</div>
          <div className="metric-body font-bold" style={{ color: colors.profit }}>
            WORKING
          </div>
        </div>
        <div>
          <div className="label-upper mb-1">Degraded since</div>
          <div className="metric-body font-bold text-text-primary">
            {since ? `${since} ET` : "—"}
          </div>
        </div>
      </div>

      <p className="text-xs text-text-dim mt-4">
        Most likely cause: the IBKR market-data subscription has lapsed. IBKR
        deducts the monthly data fee from the account cash balance in the first
        three business days of each month and switches data off when it cannot
        collect — a paper account mirrors its parent's subscriptions. Check
        Account Management → Market Data Subscriptions.
      </p>
    </div>
  );
}
