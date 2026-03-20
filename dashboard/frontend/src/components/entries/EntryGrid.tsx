import { useHydraStore, type HydraState } from "../../store/hydraStore";
import { EntryCard } from "./EntryCard";
import { colors } from "../../lib/tradingColors";
import { useShowConditionalEntries } from "../../hooks/useBotConfig";

export function EntryGrid() {
  const { hydraState } = useHydraStore();
  const entries = hydraState?.entries ?? [];
  const schedule = hydraState?.entry_schedule;
  const showConditional = useShowConditionalEntries();

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

      {/* Conditional entries: E6-E7 (MKT-035 down-day call-only) — hidden when disabled in config */}
      {showConditional && (
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
      )}
    </div>
  );
}

/** Check if a scheduled time (e.g. "11:45") has passed based on latest heartbeat ET time. */
function isTimePast(scheduledTime: string, hydraState: HydraState | null): boolean {
  if (!scheduledTime) return false;
  // last_saved is ET ISO string, e.g. "2026-03-16T13:59:10.258047-04:00"
  // Extract ET hours/minutes directly from the string to avoid timezone conversion
  const lastSaved = hydraState?.last_saved;
  if (!lastSaved) return false;
  try {
    const timeMatch = lastSaved.match(/T(\d{2}):(\d{2})/);
    if (!timeMatch) return false;
    const etHours = parseInt(timeMatch[1], 10);
    const etMinutes = parseInt(timeMatch[2], 10);
    const [schedH, schedM] = scheduledTime.split(":").map(Number);
    return etHours > schedH || (etHours === schedH && etMinutes >= schedM);
  } catch {
    return false;
  }
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
  const { hydraState } = useHydraStore();
  const past = scheduledTime ? isTimePast(scheduledTime, hydraState) : false;

  const label = past
    ? isConditional ? "not triggered" : "window passed"
    : isConditional ? "call only · scheduled" : "scheduled";

  return (
    <div
      className={`bg-card rounded-lg p-3 flex flex-col items-center justify-center min-h-[120px] ${
        isConditional
          ? "border border-dashed border-border-dim"
          : "border border-border-dim"
      }`}
      style={past ? { opacity: 0.5 } : undefined}
    >
      <span className="text-text-dim text-xs font-semibold">E{entryNum}</span>
      {scheduledTime && (
        <span className="text-text-dim text-[10px] mt-1">{scheduledTime} ET</span>
      )}
      <span
        className="text-[9px] mt-1 px-1.5 py-0.5 rounded"
        style={{ backgroundColor: `${colors.textDim}15`, color: colors.textDim }}
      >
        {label}
      </span>
    </div>
  );
}
