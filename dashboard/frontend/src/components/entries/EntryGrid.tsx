import { useHydraStore } from "../../store/hydraStore";
import { EntryCard } from "./EntryCard";
import { colors } from "../../lib/tradingColors";

export function EntryGrid() {
  const { hydraState } = useHydraStore();
  const entries = hydraState?.entries ?? [];
  const schedule = hydraState?.entry_schedule;

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
            <PendingSlot
              key={i}
              entryNum={i + 1}
              scheduledTime={schedule?.base?.[i]}
            />
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
              <PendingSlot
                key={`cond-${i}`}
                entryNum={6 + i}
                scheduledTime={schedule?.conditional?.[i]}
                isConditional
              />
            )
          )}
        </div>
      </div>
    </div>
  );
}

/** Empty placeholder card for entries not yet placed. */
function PendingSlot({
  entryNum,
  scheduledTime,
  isConditional,
}: {
  entryNum: number;
  scheduledTime?: string;
  isConditional?: boolean;
}) {
  return (
    <div
      className={`bg-card rounded-lg p-3 flex flex-col items-center justify-center min-h-[120px] ${
        isConditional
          ? "border border-dashed border-border-dim"
          : "border border-border-dim"
      }`}
    >
      <span className="text-text-dim text-xs font-semibold">E{entryNum}</span>
      {scheduledTime && (
        <span className="text-text-dim text-[10px] mt-1">{scheduledTime} ET</span>
      )}
      <span
        className="text-[9px] mt-1 px-1.5 py-0.5 rounded"
        style={{ backgroundColor: `${colors.textDim}15`, color: colors.textDim }}
      >
        {isConditional ? "call only · scheduled" : "scheduled"}
      </span>
    </div>
  );
}
