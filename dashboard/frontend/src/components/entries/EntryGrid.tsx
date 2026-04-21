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
  // As of 2026-04-17, the 10:15 slot is dropped at ALL VIX levels (max_entries [2,2,2,1]).
  // Live bot code (v1.24.0+) emits effective numbering: Entry #1 = 10:45, #2 = 11:15,
  // #3 = 14:00. Dashboard labels slots on the grid using canonical times for visual
  // continuity; dropped slots display "dropped by VIX regime". Entry cards themselves
  // use the state file's entry_number (effective), so per-card labels stay consistent.
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

  // Runtime schedule — which canonical slots actually survived the VIX regime cap?
  const activeBaseSet = new Set(schedule?.base ?? canonicalBaseTimes);
  const activeCondSet = new Set(schedule?.conditional ?? canonicalCondTimes);

  // Effective numbering (matches live bot code since 2026-04-21 rename).
  // The bot emits `entry_number` based on its position in the POST-regime
  // schedule, not the canonical schedule. So Entry #1 = first active base slot
  // (10:45 when 10:15 is dropped), Entry #2 = second, and conditional slots
  // continue the sequence.
  const activeBaseTimes = canonicalBaseTimes.filter((t) => activeBaseSet.has(t));
  const activeCondTimes = canonicalCondTimes.filter((t) => activeCondSet.has(t));
  const effectiveBaseNum = (time: string) => activeBaseTimes.indexOf(time) + 1;
  const effectiveCondNum = (time: string) =>
    activeBaseTimes.length + activeCondTimes.indexOf(time) + 1;

  // Grid column class for 1/2/3/4 active slots. Explicit strings so Tailwind
  // JIT compiler picks them up (dynamic `grid-cols-${n}` does not compile).
  const baseColsClass =
    activeBaseTimes.length === 1
      ? "grid-cols-1"
      : activeBaseTimes.length === 2
        ? "grid-cols-2"
        : activeBaseTimes.length === 3
          ? "grid-cols-3"
          : "grid-cols-4 max-lg:grid-cols-3";
  const condColsClass =
    activeCondTimes.length === 1
      ? "grid-cols-3"  // single-card width matches base-grid cell width
      : activeCondTimes.length === 2
        ? "grid-cols-2"
        : "grid-cols-3";

  return (
    <div>
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
        Entries
      </h3>
      {/* Base entries — one card per ACTIVE slot only. Canonical slots dropped by
          the VIX regime cap (e.g. 10:15 since 2026-04-17 with max_entries [2,2,2,1])
          are not rendered — the bot's effective numbering skips them, so showing
          a strikethrough placeholder only adds visual noise. If the regime ever
          re-enables a slot it will naturally reappear here. */}
      <div className={`grid gap-2 max-sm:grid-cols-1 ${baseColsClass}`}>
        {activeBaseTimes.map((canonicalTime, i) => {
          const label = `#${effectiveBaseNum(canonicalTime)}`;
          const entry = findEntryForCanonicalTime(entries, canonicalTime);
          if (entry) {
            return <EntryCard key={i} entry={entry} label={label} />;
          }
          return (
            <PendingSlot
              key={i}
              label={label}
              scheduledTime={canonicalTime}
            />
          );
        })}
      </div>

      {/* Conditional entries — one card per ACTIVE conditional slot. Header
          reflects both directions live config supports (Upday-035 put-only +
          Downday-035 call-only, both enabled since 2026-04-19). Inactive
          conditional slots are omitted (same rationale as base). */}
      {showConditional && activeCondTimes.length > 0 && (
        <div className="mt-2">
          <span className="text-[10px] text-text-dim uppercase tracking-wider">
            Conditional (Up-day ↑ put-only · Down-day ↓ call-only)
          </span>
          <div className={`grid gap-2 max-sm:grid-cols-1 mt-1 ${condColsClass}`}>
            {activeCondTimes.map((canonicalTime, i) => {
              const label = `#${effectiveCondNum(canonicalTime)}`;
              const entry = findEntryForCanonicalTime(entries, canonicalTime);
              if (entry) {
                return (
                  <EntryCard
                    key={`cond-${i}`}
                    entry={entry}
                    label={label}
                    isConditional
                  />
                );
              }
              return (
                <PendingSlot
                  key={`cond-${i}`}
                  label={label}
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

