import { statusColor } from "../../lib/tradingColors";

type EntryStatus = "active" | "expired" | "stopped" | "skipped" | "pending";

interface StatusBadgeProps {
  status: EntryStatus;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const color = statusColor(status);

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
      {status}
    </span>
  );
}
