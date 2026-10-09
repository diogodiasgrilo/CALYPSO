import { useHydraStore } from "../../store/hydraStore";
import type { HydraEntry } from "../../store/hydraStore";
import { statusColor, colors } from "../../lib/tradingColors";
import type { EntryStatus } from "../shared/StatusBadge";
import { useBotConfig, useShowConditionalEntries } from "../../hooks/useBotConfig";

// Canonical base entry slots (legacy numbering: 10:15, 10:45, 11:15).
// As of 2026-04-17, the 10:15 slot is dropped at ALL VIX levels (max_entries [2,2,2,1]).
// Live bot code (v1.24.0+) emits effective numbering: Entry #1 = 10:45, Entry #2 = 11:15.
// Dashboard still shows all 3 canonical slots on the timeline; the dropped slot
// displays "dropped by VIX regime" for visual continuity. Entry cards render using
// the state file's entry_number (which is effective), so UI labels stay consistent.
const BASE_ENTRY_TIMES = ["10:15", "10:45", "11:15"];
// Conditional 14:00 slot — live Entry #3. Fires put-only when SPX rises ≥ 0.25% (Upday-035)
// or call-only when SPX drops ≥ 0.25% (Downday-035, added 2026-04-19). Hidden when
// all conditional flags are disabled in bot config. Pre-2026-04-17 docs called this "E6".
const CONDITIONAL_ENTRY_TIMES = ["14:00"];

const TIMELINE_START = 9.5 * 60; // 9:30 in minutes
const TIMELINE_END = 16 * 60; // 16:00 in minutes
const TIMELINE_RANGE = TIMELINE_END - TIMELINE_START;

/** Below this gap (percent of the axis) two `HH:MM` labels at text-3xs touch.
 *  ~26px of text on a ~320px phone track is ~8%; 11% leaves a little air. */
const LABEL_MIN_GAP_PCT = 11;

function timeToMinutes(timeStr: string): number {
  const [h, m] = timeStr.split(":").map(Number);
  return h * 60 + m;
}

/** Smallest gap between any two adjacent slots, in percent of the axis.
 *  Returns 100 when there is nothing to collide with. */
function allSlotGapsPct(...groups: string[][]): number {
  const mins = groups
    .flat()
    .map(timeToMinutes)
    .filter((m) => Number.isFinite(m))
    .sort((a, b) => a - b);
  if (mins.length < 2) return 100;
  let min = Infinity;
  for (let i = 1; i < mins.length; i++) min = Math.min(min, mins[i] - mins[i - 1]);
  return (min / TIMELINE_RANGE) * 100;
}

function getStatus(entry: HydraEntry | undefined): EntryStatus {
  if (!entry || !entry.entry_time) return "pending";
  // 2026-07-31: a genuine execution FAILURE also sets both *_side_skipped
  // flags — must be checked before the generic skipped branch below, same
  // fix as EntryCard.tsx's getEntryStatus() (see its comment for the full
  // incident writeup).
  if (entry.execution_failed) return "failed";
  if (entry.call_side_skipped && entry.put_side_skipped) return "skipped";

  // Prefer close_reason: a Brandon TP/breach sets *_side_stopped as a generic
  // "closed" marker, so flag-inference alone mislabels a take-profit as a stop.
  const reason = (entry.close_reason || "").toUpperCase();
  if (reason === "TP") return "take_profit";
  if (reason === "BREACH") return "breach";
  // EOD safety flatten / generic early-close reuses *_side_expired/_stopped, so
  // without this it mislabels as a red stop or an expiry. Render "flattened".
  if (reason === "EOD_FLATTEN" || entry.early_closed) return "flattened";

  const callStopped = entry.call_side_stopped;
  const putStopped = entry.put_side_stopped;
  if (callStopped && putStopped) return "stopped"; // double = red
  if (callStopped || putStopped) return "stopped_single"; // single = amber

  if (entry.call_side_expired || entry.put_side_expired) return "expired";
  if (entry.entry_time) return "active";
  return "placing";
}

interface EntryTimelineProps {
  /** Polled non-primary snapshot's entries. When provided, the timeline dots
   *  resolve from THESE instead of the WS store. Omitted → WS store,
   *  byte-identical to the old behavior. */
  entries?: HydraEntry[];
}

export function EntryTimeline({ entries: entriesProp }: EntryTimelineProps = {}) {
  const { hydraState } = useHydraStore();
  const entries = entriesProp ?? hydraState?.entries ?? [];
  const showConditional = useShowConditionalEntries();
  const cfg = useBotConfig();

  // ── Whose clock is this? ─────────────────────────────────────────────────
  // `hydraState.entry_schedule` is the CANONICAL store — whichever variant
  // holds the live paper seat. Reading it here while the DOTS come from the
  // selected strategy renders one strategy's entries against another's clock.
  // Invisible until 2026-10-09 only because canonical happened to be `b`; the
  // seat moved to `bl` (12:15-15:15) and variant B-A's timeline immediately
  // showed 12:15…15:15 against its own 09:45…12:45 entries. Viewing C or H had
  // been wrong the whole time for the same reason.
  //
  // `useBotConfig()` IS scoped — it passes `strategy_id` for the current
  // selection — so it is the correct source in BOTH paths (polled non-primary
  // and WS-canonical). The store schedule is only a valid fallback when no
  // `entriesProp` was passed, i.e. we really are rendering the canonical bot.
  const storeSchedule = entriesProp ? undefined : hydraState?.entry_schedule;
  const baseTimes =
    (cfg.entry_times?.length ? cfg.entry_times : undefined) ??
    storeSchedule?.base ??
    BASE_ENTRY_TIMES;
  const condTimes =
    (cfg.conditional_entry_times?.length ? cfg.conditional_entry_times : undefined) ??
    storeSchedule?.conditional ??
    CONDITIONAL_ENTRY_TIMES;
  const baseCount = baseTimes.length;

  // ── Label collision ──────────────────────────────────────────────────────
  // Labels are absolutely positioned at `left: pct%` and centred, so their
  // spacing is set by the schedule, not by layout. A 30-minute gap is only
  // 7.7% of the 09:30-16:00 axis — about 25px at phone width, against a
  // ~26px "12:15" at text-3xs. They overlap into "12:1512:4513:15…".
  // Seven slots made it unmissable; two adjacent slots were already marginal.
  // Stagger onto two rows rather than dropping labels: every time stays
  // readable, which is the point of the row.
  const minGapPct = allSlotGapsPct(baseTimes, showConditional ? condTimes : []);
  const dense = minGapPct < LABEL_MIN_GAP_PCT;

  return (
    <div>
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
        Timeline
      </h3>
      <div className="bg-card rounded-lg border border-border-dim p-3">
        <div className={`relative ${dense ? "h-12" : "h-8"}`}>
          {/* Track line */}
          <div className="absolute top-1/2 left-0 right-0 h-px bg-border" />

          {/* Time labels */}
          <span className="absolute left-0 -top-1 text-3xs text-text-dim">
            9:30
          </span>
          <span className="absolute right-0 -top-1 text-3xs text-text-dim">
            16:00
          </span>

          {/* Base entry dots — read from state schedule */}
          {baseTimes.map((time, i) => {
            const minutes = timeToMinutes(time);
            const pct = ((minutes - TIMELINE_START) / TIMELINE_RANGE) * 100;
            const entryNum = i + 1;
            const entry = entries.find((e) => e.entry_number === entryNum);
            const status = getStatus(entry);
            const color = statusColor(status);

            return (
              <div
                key={`base-${i}`}
                className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 flex flex-col items-center"
                style={{ left: `${pct}%` }}
              >
                <div
                  className={`w-3 h-3 rounded-full border-2 ${
                    status === "active" ? "pulse-live" : ""
                  }`}
                  style={{
                    backgroundColor:
                      status === "pending" ? "transparent" : color,
                    borderColor: color,
                  }}
                  title={`E${entryNum} ${time} — ${status}`}
                />
                <span
                  className={`text-3xs text-text-dim ${
                    dense && i % 2 === 1 ? "mt-[15px]" : "mt-1"
                  }`}
                >
                  {time}
                </span>
              </div>
            );
          })}

          {/* Conditional entry dots — entry numbers follow base count dynamically */}
          {showConditional && condTimes.map((time, i) => {
            const minutes = timeToMinutes(time);
            const pct = ((minutes - TIMELINE_START) / TIMELINE_RANGE) * 100;
            const entryNum = baseCount + 1 + i;
            const entry = entries.find((e) => e.entry_number === entryNum);
            const status = getStatus(entry);
            const color = statusColor(status);
            const isPending = status === "pending";

            return (
              <div
                key={`cond-${i}`}
                className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 flex flex-col items-center"
                style={{ left: `${pct}%` }}
              >
                {/* Diamond shape for conditional entries */}
                <div
                  className={`w-3 h-3 rotate-45 ${
                    status === "active" ? "pulse-live" : ""
                  }`}
                  style={{
                    backgroundColor: isPending ? "transparent" : color,
                    border: `2px ${isPending ? "dashed" : "solid"} ${isPending ? colors.textDim : color}`,
                  }}
                  title={`E${entryNum} ${time} — conditional — ${status}`}
                />
                <span
                  className={`text-3xs ${dense ? "mt-[15px]" : "mt-1"}`}
                  style={{ color: isPending ? colors.textDim : colors.textSecondary }}
                >
                  {time}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
