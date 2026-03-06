import { statusColor } from "../../lib/tradingColors";

export type EntryStatus =
  | "active"
  | "expired"
  | "stopped"
  | "stopped_single"
  | "skipped"
  | "pending"
  | "placing";

interface StatusBadgeProps {
  status: EntryStatus;
  /** For single stops, which side was stopped ("call" | "put") */
  stoppedSide?: "call" | "put";
}

function getLabel(status: EntryStatus, stoppedSide?: "call" | "put"): string {
  if (status === "stopped_single" && stoppedSide) {
    return stoppedSide === "call" ? "Call Stopped" : "Put Stopped";
  }
  if (status === "stopped") return "Double Stop";
  return status;
}

export function StatusBadge({ status, stoppedSide }: StatusBadgeProps) {
  const color = statusColor(status);
  const label = getLabel(status, stoppedSide);

  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider ${
        status === "active" ? "pulse-live" : ""
      }`}
      style={{
        backgroundColor: `${color}20`,
        color,
      }}
    >
      {status === "active" && (
        <span
          className="w-1.5 h-1.5 rounded-full"
          style={{ backgroundColor: color }}
        />
      )}
      {label}
    </span>
  );
}
