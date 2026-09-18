import { statusColor } from "../../lib/tradingColors";

export type EntryStatus =
  | "active"
  | "expired"
  | "flattened"
  | "take_profit"
  | "breach"
  | "stopped"
  | "stopped_single"
  | "skipped"
  | "failed"
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
  if (status === "take_profit") return "Take Profit";
  if (status === "breach") return "Breach Exit";
  if (status === "flattened") return "Flattened";
  if (status === "failed") return "Execution Failed";
  return status;
}

/** The status colour at 22% over BLACK — a solid, opaque pill.
 *
 * The pill used to be `${color}20`: the status colour at 12.5% alpha over
 * whatever surface it happened to sit on. A SELF-tint lifts the background
 * toward the text and costs contrast, and no alpha fixes it — `loss` on a card
 * is 4.51:1, so ANY self-tint pushes it under AA. Measured 2026-09-18, 7 of the
 * 11 statuses failed, worst 3.88:1 for `stopped` (Double Stop) and `failed`
 * (Execution Failed), the two most operationally important badges.
 *
 * Only `skipped` ever showed up in the visual audit, because the fixtures never
 * produce the other ten states — the same blind spot as the calendar's
 * max-intensity cell and the low-cushion readout.
 *
 * Mixing toward black instead makes the pill strictly darker than any surface,
 * so the result is surface-INDEPENDENT (4.5:1 holds on card, bg, bg-elevated
 * alike) and carries more hue than the old 12.5% wash, not less. Worst case is
 * now 5.33:1. Opaque rather than a scrim on purpose: a `backgroundImage`
 * gradient is invisible to `getComputedStyle().backgroundColor`, so the
 * contrast probe could not see it and reported a fixed badge as still failing.
 */
function pillBackground(hex: string): string {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16);
  if (Number.isNaN(n)) return "rgb(24, 28, 33)";
  const mix = (v: number) => Math.round(v * 0.22);
  return `rgb(${mix((n >> 16) & 255)}, ${mix((n >> 8) & 255)}, ${mix(n & 255)})`;
}

export function StatusBadge({ status, stoppedSide }: StatusBadgeProps) {
  const color = statusColor(status);
  const label = getLabel(status, stoppedSide);

  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-3xs font-semibold uppercase tracking-wider ${
        status === "active" ? "pulse-live" : ""
      }`}
      style={{
        backgroundColor: pillBackground(color),
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
