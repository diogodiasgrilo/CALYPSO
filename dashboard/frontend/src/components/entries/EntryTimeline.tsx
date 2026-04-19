import { useHydraStore } from "../../store/hydraStore";
import type { HydraEntry } from "../../store/hydraStore";
import { statusColor, colors } from "../../lib/tradingColors";
import type { EntryStatus } from "../shared/StatusBadge";
import { useShowConditionalEntries } from "../../hooks/useBotConfig";

// Canonical base entry slots: E1=10:15, E2=10:45, E3=11:15.
// As of 2026-04-17, E#1 (10:15) is dropped at ALL VIX levels (max_entries [2,2,2,1]).
// Dashboard still shows all 3 canonical slots; dropped slots display "dropped by VIX regime".
const BASE_ENTRY_TIMES = ["10:15", "10:45", "11:15"];
// Conditional entries: E6 (14:00) fires put-only when SPX rises ≥ 0.25% from open (Upday-035)
// Hidden when all conditional flags are disabled in bot config
const CONDITIONAL_ENTRY_TIMES = ["14:00"];

const TIMELINE_START = 9.5 * 60; // 9:30 in minutes
const TIMELINE_END = 16 * 60; // 16:00 in minutes
const TIMELINE_RANGE = TIMELINE_END - TIMELINE_START;

function timeToMinutes(timeStr: string): number {
  const [h, m] = timeStr.split(":").map(Number);
  return h * 60 + m;
}

function getStatus(entry: HydraEntry | undefined): EntryStatus {
  if (!entry || !entry.entry_time) return "pending";
  if (entry.call_side_skipped && entry.put_side_skipped) return "skipped";

  const callStopped = entry.call_side_stopped;
  const putStopped = entry.put_side_stopped;
  if (callStopped && putStopped) return "stopped"; // double = red
  if (callStopped || putStopped) return "stopped_single"; // single = amber

  if (entry.call_side_expired || entry.put_side_expired) return "expired";
  if (entry.entry_time) return "active";
  return "placing";
}

export function EntryTimeline() {
  const { hydraState } = useHydraStore();
  const entries = hydraState?.entries ?? [];
  const showConditional = useShowConditionalEntries();
  const schedule = hydraState?.entry_schedule;

  // Read times from state schedule (falls back to hardcoded if no schedule)
  const baseTimes = schedule?.base ?? BASE_ENTRY_TIMES;
  const condTimes = schedule?.conditional ?? CONDITIONAL_ENTRY_TIMES;
  const baseCount = baseTimes.length;

  return (
    <div>
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
        Timeline
      </h3>
      <div className="bg-card rounded-lg border border-border-dim p-3">
        <div className="relative h-8">
          {/* Track line */}
          <div className="absolute top-1/2 left-0 right-0 h-px bg-border" />

          {/* Time labels */}
          <span className="absolute left-0 -top-1 text-[10px] text-text-dim">
            9:30
          </span>
          <span className="absolute right-0 -top-1 text-[10px] text-text-dim">
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
                <span className="text-[9px] text-text-dim mt-1">{time}</span>
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
                <span className="text-[9px] mt-1" style={{ color: isPending ? colors.textDim : colors.textSecondary }}>
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
