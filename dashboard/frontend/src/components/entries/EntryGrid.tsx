import { useHydraStore, type HydraState } from "../../store/hydraStore";
import { EntryCard } from "./EntryCard";
import { colors } from "../../lib/tradingColors";
import { useShowConditionalEntries } from "../../hooks/useBotConfig";

export function EntryGrid() {
  const { hydraState } = useHydraStore();
  const entries = hydraState?.entries ?? [];
  const schedule = hydraState?.entry_schedule;
  const showConditional = useShowConditionalEntries();

  // Base entry slots — count from state schedule (default 3: E1-E3)
  const baseCount = schedule?.base?.length ?? 3;
  const baseSlots = Array.from({ length: baseCount }, (_, i) =>
    entries.find((e) => e.entry_number === i + 1) ?? null
  );

  // Conditional entry slots — entry numbers start after base count (not hardcoded 6)
  // VIX regime can cap base entries (e.g., 2 instead of 3), shifting conditional numbers
  const condCount = schedule?.conditional?.length ?? 0;
  const conditionalSlots = Array.from({ length: condCount }, (_, i) =>
    entries.find((e) => e.entry_number === baseCount + 1 + i) ?? null
  );

  return (
    <div>
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
        Entries
      </h3>
      {/* Base entries — grid adapts to count from schedule */}
      <div className={`grid gap-2 max-sm:grid-cols-1 ${baseCount <= 3 ? "grid-cols-3" : "grid-cols-4 max-lg:grid-cols-3"}`}>
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

      {/* Conditional entries — only shows slots from schedule (E7 disabled = hidden) */}
      {showConditional && condCount > 0 && (
        <div className="mt-2">
          <span className="text-[10px] text-text-dim uppercase tracking-wider">
            Conditional (E6: up-day ↑ put-only{condCount > 1 ? " · E7: down-day ↓ call-only" : ""})
          </span>
          <div className={`grid gap-2 max-sm:grid-cols-1 mt-1 ${condCount === 1 ? "grid-cols-3" : "grid-cols-5 max-lg:grid-cols-3"}`}>
            {conditionalSlots.map((entry, i) =>
              entry ? (
                <EntryCard key={`cond-${i}`} entry={entry} isConditional />
              ) : (
                <PendingSlot
                  key={`cond-${i}`}
                  entryNum={baseCount + 1 + i}
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
    : isConditional
      ? "conditional · scheduled"
      : "scheduled";

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
