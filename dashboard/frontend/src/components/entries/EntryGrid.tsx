import { useHydraStore, type HydraEntry, type HydraState } from "../../store/hydraStore";
import { EntryCard } from "./EntryCard";
import { colors } from "../../lib/tradingColors";
import { useBotConfig, useShowConditionalEntries } from "../../hooks/useBotConfig";

/** Extract HH:MM from an ET ISO timestamp (e.g. "2026-04-14T10:45:12.123-04:00") or "HH:MM" string. */
function extractHHMM(s: string | null | undefined): string | null {
  if (!s) return null;
  const m = s.match(/(\d{2}):(\d{2})/);
  return m ? `${m[1]}:${m[2]}` : null;
}

/** Find the entry whose placement time matches the canonical slot time (HH:MM). */
function findEntryForCanonicalTime(
  entries: HydraEntry[],
  canonicalTime: string
): HydraEntry | null {
  for (const e of entries) {
    const hhmm = extractHHMM(e.entry_time);
    if (hhmm === canonicalTime) return e;
  }
  return null;
}

export function EntryGrid() {
  const { hydraState } = useHydraStore();
  const config = useBotConfig();
  const entries: HydraEntry[] = hydraState?.entries ?? [];
  const schedule = hydraState?.entry_schedule;
  const showConditional = useShowConditionalEntries();

  // Canonical base times (pre-VIX-cap) come from bot config.
  // As of 2026-04-17, E#1 (10:15) is dropped at ALL VIX levels (max_entries [2,2,2,1]).
  // Canonical slots E1/E2/E3 still labelled for visual continuity; dropped slots show "dropped by VIX regime".
  // Fall back to the current (possibly capped) state schedule, or hardcoded defaults,
  // when the bot-config fetch hasn't completed yet.
  const canonicalBaseTimes: string[] =
    config.entry_times.length > 0
      ? config.entry_times
      : schedule?.base ?? ["10:15", "10:45", "11:15"];
  const canonicalCondTimes: string[] =
    config.conditional_entry_times.length > 0
      ? config.conditional_entry_times
      : schedule?.conditional ?? [];

  const baseCount = canonicalBaseTimes.length;

  // Runtime schedule — which canonical slots actually survived the VIX regime cap?
  const activeBaseSet = new Set(schedule?.base ?? canonicalBaseTimes);
  const activeCondSet = new Set(schedule?.conditional ?? canonicalCondTimes);

  return (
    <div>
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
        Entries
      </h3>
      {/* Base entries — one card per canonical slot. Dropped slots show "dropped by VIX regime". */}
      <div
        className={`grid gap-2 max-sm:grid-cols-1 ${
          baseCount <= 3 ? "grid-cols-3" : "grid-cols-4 max-lg:grid-cols-3"
        }`}
      >
        {canonicalBaseTimes.map((canonicalTime, i) => {
          const canonicalLabel = `E${i + 1}`;
          const isActive = activeBaseSet.has(canonicalTime);

          if (!isActive) {
            return (
              <DroppedSlot key={i} label={canonicalLabel} scheduledTime={canonicalTime} />
            );
          }

          const entry = findEntryForCanonicalTime(entries, canonicalTime);
          if (entry) {
            return <EntryCard key={i} entry={entry} label={canonicalLabel} />;
          }
          return (
            <PendingSlot
              key={i}
              label={canonicalLabel}
              scheduledTime={canonicalTime}
            />
          );
        })}
      </div>

      {/* Conditional entries — one card per canonical conditional slot. Header intentionally
          omits the canonical number because the bot's conditional-slot naming has evolved
          (E6/E7 historically, E4 now after the 3-base reconvergence); the time + direction
          are what actually matter for the user. */}
      {showConditional && canonicalCondTimes.length > 0 && (
        <div className="mt-2">
          <span className="text-[10px] text-text-dim uppercase tracking-wider">
            Conditional (Up-day ↑ put-only
            {canonicalCondTimes.length > 1 ? " · Down-day ↓ call-only" : ""})
          </span>
          <div
            className={`grid gap-2 max-sm:grid-cols-1 mt-1 ${
              canonicalCondTimes.length === 1
                ? "grid-cols-3"
                : "grid-cols-5 max-lg:grid-cols-3"
            }`}
          >
            {canonicalCondTimes.map((canonicalTime, i) => {
              const canonicalLabel = `E${baseCount + i + 1}`;
              const isActive = activeCondSet.has(canonicalTime);

              if (!isActive) {
                return (
                  <DroppedSlot
                    key={`cond-${i}`}
                    label={canonicalLabel}
                    scheduledTime={canonicalTime}
                    isConditional
                  />
                );
              }

              const entry = findEntryForCanonicalTime(entries, canonicalTime);
              if (entry) {
                return (
                  <EntryCard
                    key={`cond-${i}`}
                    entry={entry}
                    label={canonicalLabel}
                    isConditional
                  />
                );
              }
              return (
                <PendingSlot
                  key={`cond-${i}`}
                  label={canonicalLabel}
                  scheduledTime={canonicalTime}
                  isConditional
                />
              );
            })}
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
  label,
  scheduledTime,
  isConditional,
}: {
  label: string;
  scheduledTime?: string;
  isConditional?: boolean;
}) {
  const { hydraState } = useHydraStore();
  const past = scheduledTime ? isTimePast(scheduledTime, hydraState) : false;

  const badge = past
    ? isConditional
      ? "not triggered"
      : "window passed"
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
      <span className="text-text-dim text-xs font-semibold">{label}</span>
      {scheduledTime && (
        <span className="text-text-dim text-[10px] mt-1">{scheduledTime} ET</span>
      )}
      <span
        className="text-[9px] mt-1 px-1.5 py-0.5 rounded"
        style={{ backgroundColor: `${colors.textDim}15`, color: colors.textDim }}
      >
        {badge}
      </span>
    </div>
  );
}

/** Canonical slot that was dropped at runtime by the VIX regime cap. */
function DroppedSlot({
  label,
  scheduledTime,
  isConditional,
}: {
  label: string;
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
      style={{ opacity: 0.4 }}
    >
      <span className="text-text-dim text-xs font-semibold line-through">{label}</span>
      {scheduledTime && (
        <span className="text-text-dim text-[10px] mt-1">{scheduledTime} ET</span>
      )}
      <span
        className="text-[9px] mt-1 px-1.5 py-0.5 rounded text-center leading-tight"
        style={{ backgroundColor: `${colors.warning}20`, color: colors.warning }}
      >
        dropped by VIX regime
      </span>
    </div>
  );
}
