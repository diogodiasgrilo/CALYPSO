import { useHydraStore } from "../../store/hydraStore";
import type { HydraEntry } from "../../store/hydraStore";
import { statusColor } from "../../lib/tradingColors";
import type { EntryStatus } from "../shared/StatusBadge";

const ENTRY_TIMES = ["11:15", "11:45", "12:15", "12:45", "13:15"];
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
  // Entry has entry_time → it was placed and is active (is_complete may be null in state file)
  if (entry.entry_time) return "active";
  return "placing";
}

export function EntryTimeline() {
  const { hydraState } = useHydraStore();
  const entries = hydraState?.entries ?? [];

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

          {/* Entry dots */}
          {ENTRY_TIMES.map((time, i) => {
            const minutes = timeToMinutes(time);
            const pct = ((minutes - TIMELINE_START) / TIMELINE_RANGE) * 100;
            const entry = entries.find((e) => e.entry_number === i + 1);
            const status = getStatus(entry);
            const color = statusColor(status);

            return (
              <div
                key={i}
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
                  title={`E${i + 1} ${time} — ${status}`}
                />
                <span className="text-[9px] text-text-dim mt-1">{time}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
