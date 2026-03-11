import { useHydraStore } from "../../store/hydraStore";
import { EntryCard } from "./EntryCard";

export function EntryGrid() {
  const { hydraState } = useHydraStore();
  const entries = hydraState?.entries ?? [];

  // 5 base entry slots (E1-E5)
  const baseSlots = Array.from({ length: 5 }, (_, i) =>
    entries.find((e) => e.entry_number === i + 1) ?? null
  );

  // 2 conditional entry slots (E6-E7) — MKT-035 call-only entries
  const conditionalSlots = Array.from({ length: 2 }, (_, i) =>
    entries.find((e) => e.entry_number === 6 + i) ?? null
  );

  return (
    <div>
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
        Entries
      </h3>
      {/* Base entries: E1-E5 */}
      <div className="grid grid-cols-5 gap-2 max-lg:grid-cols-3 max-sm:grid-cols-1">
        {baseSlots.map((entry, i) =>
          entry ? (
            <EntryCard key={i} entry={entry} />
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

      {/* Conditional entries: E6-E7 (MKT-035 down-day call-only) */}
      <div className="mt-2">
        <span className="text-[10px] text-text-dim uppercase tracking-wider">
          Conditional (MKT-035 down day)
        </span>
        <div className="grid grid-cols-5 gap-2 max-lg:grid-cols-3 max-sm:grid-cols-1 mt-1">
          {conditionalSlots.map((entry, i) =>
            entry ? (
              <EntryCard key={`cond-${i}`} entry={entry} isConditional />
            ) : (
              <div
                key={`cond-${i}`}
                className="bg-card rounded-lg border border-dashed border-border-dim p-3 flex items-center justify-center min-h-[120px]"
              >
                <div className="text-center">
                  <span className="text-text-dim text-xs block">E{6 + i}</span>
                  <span className="text-[9px] text-text-dim">call only</span>
                </div>
              </div>
            )
          )}
        </div>
      </div>
    </div>
  );
}
