import { useMemo } from "react";
import { useHydraStore } from "../../store/hydraStore";
import { EntryCard } from "./EntryCard";
import type { HydraEntry } from "../../store/hydraStore";

/**
 * Compute per-entry slippage correction.
 *
 * The state file only has aggregate total_realized_pnl (actual execution prices).
 * Per-entry stopped side P&L uses theoretical stop_level - credit (trigger level).
 * The difference includes: slippage from market orders + long leg salvage revenue.
 * Distribute proportionally among stopped entries.
 */
function computeSlippageMap(
  entries: HydraEntry[],
  totalRealizedPnl: number,
): Map<number, number> {
  const map = new Map<number, number>();

  // Compute theoretical realized P&L per entry
  let theoreticalRealizedTotal = 0;
  const stoppedEntryNumbers: number[] = [];
  const theoreticalStopLoss: Map<number, number> = new Map();

  for (const e of entries) {
    if (!e.entry_time) continue;
    let entryRealized = 0;

    // Expired sides: credit kept (realized profit)
    if (e.call_side_expired) entryRealized += e.call_spread_credit;
    if (e.put_side_expired) entryRealized += e.put_spread_credit;

    // Stopped sides: theoretical loss (using trigger level, not actual execution)
    let entryStopLoss = 0;
    if (e.call_side_stopped) {
      const loss = Math.max(0, e.call_side_stop - e.call_spread_credit);
      entryRealized -= loss;
      entryStopLoss += loss;
    }
    if (e.put_side_stopped) {
      const loss = Math.max(0, e.put_side_stop - e.put_spread_credit);
      entryRealized -= loss;
      entryStopLoss += loss;
    }

    // Long leg salvage revenue (already in total_realized_pnl, add to theoretical too)
    entryRealized += (e.call_long_sold_revenue ?? 0) + (e.put_long_sold_revenue ?? 0);

    theoreticalRealizedTotal += entryRealized;
    if (entryStopLoss > 0) {
      stoppedEntryNumbers.push(e.entry_number);
      theoreticalStopLoss.set(e.entry_number, entryStopLoss);
    }
  }

  // Total slippage = actual - theoretical
  const totalSlippage = totalRealizedPnl - theoreticalRealizedTotal;

  // Distribute slippage proportionally among stopped entries
  if (stoppedEntryNumbers.length === 0 || Math.abs(totalSlippage) < 0.01) {
    return map;
  }

  const totalTheoreticalLoss = Array.from(theoreticalStopLoss.values()).reduce((a, b) => a + b, 0);
  for (const entryNum of stoppedEntryNumbers) {
    const entryLoss = theoreticalStopLoss.get(entryNum) ?? 0;
    const proportion = totalTheoreticalLoss > 0 ? entryLoss / totalTheoreticalLoss : 1 / stoppedEntryNumbers.length;
    map.set(entryNum, totalSlippage * proportion);
  }

  return map;
}

export function EntryGrid() {
  const { hydraState } = useHydraStore();
  const entries = hydraState?.entries ?? [];
  const totalRealizedPnl = hydraState?.total_realized_pnl ?? 0;

  const slippageMap = useMemo(
    () => computeSlippageMap(entries, totalRealizedPnl),
    [entries, totalRealizedPnl],
  );

  // Pad to 5 slots
  const slots = Array.from({ length: 5 }, (_, i) =>
    entries.find((e) => e.entry_number === i + 1) ?? null
  );

  return (
    <div>
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
        Entries
      </h3>
      <div className="grid grid-cols-5 gap-2 max-lg:grid-cols-3 max-sm:grid-cols-1">
        {slots.map((entry, i) =>
          entry ? (
            <EntryCard
              key={i}
              entry={entry}
              slippageCorrection={slippageMap.get(entry.entry_number) ?? 0}
            />
          ) : (
            <div
              key={i}
              className="bg-card rounded-lg border border-border-dim p-3 flex items-center justify-center min-h-[120px]"
            >
              <span className="text-text-dim text-xs">E{i + 1}</span>
            </div>
          )
        )}
      </div>
    </div>
  );
}
