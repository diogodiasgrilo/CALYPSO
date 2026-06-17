import { useHydraStore, type HydraEntry } from "../../store/hydraStore";
import { colors, cushionColor } from "../../lib/tradingColors";

function computeCushion(spreadValue: number, stopLevel: number): number {
  if (!stopLevel || stopLevel <= 0) return 100;
  return Math.max(0, Math.min(100, ((stopLevel - spreadValue) / stopLevel) * 100));
}

interface PositionHeatmapProps {
  /** Polled non-primary snapshot's entries. When provided, the map renders
   *  THESE instead of the WS store's. Omitted → WS store. */
  entries?: HydraEntry[];
  /** Live SPX spot for the polled path (the snapshot's spx_price). The WS path
   *  derives SPX from the last OHLC bar; off-primary we pass the snapshot value
   *  since the store's OHLC belongs to the primary. */
  spx?: number;
}

export function PositionHeatmap({ entries: entriesProp, spx: spxProp }: PositionHeatmapProps = {}) {
  const hydraState = useHydraStore((s) => s.hydraState);
  const todayOHLC = useHydraStore((s) => s.todayOHLC);

  const usingProps = entriesProp !== undefined;
  const entries = entriesProp ?? hydraState?.entries ?? [];
  const activeEntries = entries.filter((e) => {
    if (!e.entry_time) return false;
    const callDone = e.call_side_stopped || e.call_side_expired || e.call_side_skipped;
    const putDone = e.put_side_stopped || e.put_side_expired || e.put_side_skipped;
    return !callDone || !putDone; // at least one side still active
  });

  if (activeEntries.length === 0) return null;

  // Current SPX: prop mode uses the snapshot's spx_price; WS mode reads the
  // last OHLC bar's close exactly as before.
  const lastBar = todayOHLC.length > 0 ? todayOHLC[todayOHLC.length - 1] : null;
  const spx = usingProps ? (spxProp ?? 0) : (lastBar?.close ?? 0);

  // Find strike range across all entries
  const allStrikes: number[] = [];
  for (const e of activeEntries) {
    if (e.short_put_strike > 0) allStrikes.push(e.short_put_strike, e.long_put_strike);
    if (e.short_call_strike > 0) allStrikes.push(e.short_call_strike, e.long_call_strike);
  }
  if (allStrikes.length === 0) return null;

  // Include the live SPX in the axis range so the spot marker is always on-chart
  // and the axis can't read "7450 … SPX 7547 … 7485" (spot outside the strikes,
  // e.g. far-OTM put-only entries). Pad 10pt on each side.
  const axisPoints = spx > 0 ? [...allStrikes, spx] : allStrikes;
  const minStrike = Math.min(...axisPoints) - 10;
  const maxStrike = Math.max(...axisPoints) + 10;
  const range = maxStrike - minStrike;
  if (range <= 0) return null;

  const toX = (strike: number) => ((strike - minStrike) / range) * 100;
  const spxX = spx > 0 ? toX(spx) : -1;

  return (
    <div>
      <h3 className="label-upper mb-2">Position Map</h3>
      <div className="bg-card rounded-lg border border-border-dim p-3">
        <div className="relative" style={{ height: activeEntries.length * 28 + 20 }}>
          {/* SPX vertical line */}
          {spxX >= 0 && spxX <= 100 && (
            <div
              className="absolute top-0 bottom-0 w-px"
              style={{
                left: `${spxX}%`,
                backgroundColor: colors.textPrimary,
                opacity: 0.5,
              }}
            />
          )}

          {/* Entries */}
          {activeEntries.map((e, i) => {
            const y = i * 28 + 4;
            const putCushion = computeCushion(e.put_spread_value ?? 0, e.effective_put_stop ?? e.put_side_stop);
            const callCushion = computeCushion(e.call_spread_value ?? 0, e.effective_call_stop ?? e.call_side_stop);
            const hasPut = e.short_put_strike > 0 && !e.put_side_stopped && !e.put_side_skipped;
            const hasCall = e.short_call_strike > 0 && !e.call_side_stopped && !e.call_side_skipped;

            return (
              <div key={e.entry_number} className="absolute left-0 right-0" style={{ top: y }}>
                {/* Entry label */}
                <span
                  className="absolute text-[10px] font-semibold text-text-dim"
                  style={{ left: 0, top: 2 }}
                >
                  E{e.entry_number}
                </span>

                {/* Put spread */}
                {hasPut && e.long_put_strike > 0 && (
                  <div
                    className="absolute h-4 rounded-sm"
                    style={{
                      left: `${Math.max(3, toX(e.long_put_strike))}%`,
                      width: `${Math.max(1, toX(e.short_put_strike) - toX(e.long_put_strike))}%`,
                      backgroundColor: cushionColor(putCushion),
                      opacity: 0.6,
                      top: 0,
                    }}
                  />
                )}

                {/* Call spread */}
                {hasCall && e.long_call_strike > 0 && (
                  <div
                    className="absolute h-4 rounded-sm"
                    style={{
                      left: `${Math.max(3, toX(e.short_call_strike))}%`,
                      width: `${Math.max(1, toX(e.long_call_strike) - toX(e.short_call_strike))}%`,
                      backgroundColor: cushionColor(callCushion),
                      opacity: 0.6,
                      top: 0,
                    }}
                  />
                )}
              </div>
            );
          })}

          {/* Strike axis */}
          <div
            className="absolute left-0 right-0 flex justify-between text-[9px] text-text-dim"
            style={{ bottom: 0 }}
          >
            <span>{minStrike}</span>
            {spx > 0 && <span className="text-text-primary font-semibold">SPX {spx.toFixed(0)}</span>}
            <span>{maxStrike}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
