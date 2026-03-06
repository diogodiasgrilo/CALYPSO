/** Formatting utilities for trading data display. */

/** Format a dollar P&L value with sign and color class. */
export function formatPnL(value: number, decimals = 2): string {
  const sign = value >= 0 ? "+" : "";
  return `${sign}$${Math.abs(value).toFixed(decimals)}`;
}

/** Format a number as currency. */
export function formatCurrency(value: number, decimals = 2): string {
  return `$${value.toFixed(decimals)}`;
}

/** Format SPX price (2 decimal places). */
export function formatPrice(value: number): string {
  return value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Format time from ISO string to HH:MM ET. */
export function formatTime(iso: string): string {
  if (!iso) return "--:--";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "America/New_York",
    });
  } catch {
    // Try parsing as "YYYY-MM-DD HH:MM:SS" (HYDRA log format)
    const match = iso.match(/(\d{2}:\d{2})/);
    return match ? match[1] : "--:--";
  }
}

/** Format date as short (Mar 6). */
export function formatDateShort(dateStr: string): string {
  if (!dateStr) return "";
  const d = new Date(dateStr + "T12:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** Win rate as percentage from wins and losses. */
export function winRate(wins: number, losses: number): string {
  const total = wins + losses;
  if (total === 0) return "0%";
  return `${((wins / total) * 100).toFixed(1)}%`;
}
