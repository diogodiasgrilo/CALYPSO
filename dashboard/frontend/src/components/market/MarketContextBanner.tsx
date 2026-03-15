import { useHydraStore } from "../../store/hydraStore";
import { formatPnL, winRate } from "../../lib/formatters";
import { pnlColor, colors } from "../../lib/tradingColors";
import { Calendar, Clock, AlertTriangle, Sun, Coffee } from "lucide-react";

/** Format next_open ISO string as readable date/time. */
function formatNextOpen(isoStr: string | undefined): string {
  if (!isoStr) return "next trading day";
  try {
    const d = new Date(isoStr);
    const day = d.toLocaleDateString("en-US", {
      weekday: "long",
      timeZone: "America/New_York",
    });
    return `${day} at 9:30 AM ET`;
  } catch {
    return "next trading day";
  }
}

/** FOMC alert strip — rendered as overlay above any state. */
export function FOMCBanner() {
  const market = useHydraStore((s) => s.market);
  if (!market) return null;

  const isFomcDay = market.is_fomc_day;
  const isAnnouncement = market.is_fomc_announcement;
  const daysUntil = market.days_until_fomc;

  // Show on FOMC day or 1-2 days before
  if (!isFomcDay && (daysUntil == null || daysUntil > 2 || daysUntil <= 0)) return null;

  return (
    <div
      className="rounded-lg border px-4 py-2.5 flex items-center gap-3"
      style={{
        backgroundColor: "rgba(210, 153, 34, 0.08)",
        borderColor: "rgba(210, 153, 34, 0.25)",
      }}
    >
      <AlertTriangle size={16} style={{ color: colors.warning }} className="shrink-0" />
      <div className="text-xs">
        {isAnnouncement ? (
          <span style={{ color: colors.warning }} className="font-semibold">
            FOMC Announcement Day — Rate Decision at 2:00 PM ET
          </span>
        ) : isFomcDay ? (
          <span style={{ color: colors.warning }} className="font-semibold">
            FOMC Meeting Day 1 — Announcement Tomorrow at 2:00 PM ET
          </span>
        ) : (
          <span className="text-text-secondary">
            FOMC Meeting in{" "}
            <span style={{ color: colors.warning }} className="font-semibold">
              {daysUntil} day{daysUntil !== 1 ? "s" : ""}
            </span>
          </span>
        )}
        {isFomcDay && (
          <span className="text-text-dim ml-2">
            — HYDRA skips all entries on FOMC days
          </span>
        )}
      </div>
    </div>
  );
}

/** Main context banner — shown in compact layout (market closed, no data). */
export function MarketContextBanner() {
  const market = useHydraStore((s) => s.market);
  const metrics = useHydraStore((s) => s.metrics);

  if (!market) return null;
  // Don't show banner when market is open — full layout handles that
  if (market.is_open) return null;

  const cumulativePnl = metrics?.cumulative_pnl ?? 0;
  const winningDays = metrics?.winning_days ?? 0;
  const losingDays = metrics?.losing_days ?? 0;
  const totalDays = winningDays + losingDays;
  const nextOpen = formatNextOpen(market.next_event?.next_open);

  // Determine context
  const isWeekend = !market.is_trading_day && !market.holiday_name;
  const isHoliday = !market.is_trading_day && !!market.holiday_name;
  const isPreMarket = market.is_trading_day && market.session === "pre_market";
  const isPostMarket = market.is_trading_day && !market.is_open;

  // Badge config
  let badge = "CLOSED";
  let badgeColor: string = colors.textDim;
  let Icon = Clock;
  let subtitle = `Market opens ${nextOpen}`;

  if (isWeekend) {
    badge = "WEEKEND";
    badgeColor = colors.textDim;
    Icon = Sun;
  } else if (isHoliday) {
    badge = "MARKET HOLIDAY";
    badgeColor = colors.info;
    Icon = Calendar;
    subtitle = `${market.holiday_name} — Market opens ${nextOpen}`;
  } else if (isPreMarket) {
    badge = "PRE-MARKET";
    badgeColor = colors.info;
    Icon = Coffee;
    if (market.is_early_close && market.early_close_reason) {
      subtitle = `Market opens at 9:30 AM ET — Early close today (${market.early_close_reason})`;
    } else {
      subtitle = "Market opens at 9:30 AM ET";
    }
  } else if (isPostMarket) {
    badge = "MARKET CLOSED";
    badgeColor = colors.textSecondary;
    Icon = Clock;
  }

  return (
    <div className="bg-card rounded-lg border border-border-dim p-6">
      {/* Badge */}
      <div className="flex items-center gap-3 mb-4">
        <Icon size={20} style={{ color: badgeColor }} />
        <span
          className="text-xs font-bold uppercase tracking-widest"
          style={{ color: badgeColor }}
        >
          {badge}
        </span>
      </div>

      {/* Subtitle */}
      <p className="text-sm text-text-secondary mb-4">{subtitle}</p>

      {/* Cumulative stats */}
      {totalDays > 0 && (
        <div className="grid grid-cols-4 gap-4 max-sm:grid-cols-2">
          <div>
            <div className="label-upper mb-1">Cumulative P&L</div>
            <div
              className="metric-body font-bold"
              style={{ color: pnlColor(cumulativePnl) }}
            >
              {formatPnL(cumulativePnl)}
            </div>
          </div>
          <div>
            <div className="label-upper mb-1">Trading Days</div>
            <div className="metric-body text-text-primary">{totalDays}</div>
          </div>
          <div>
            <div className="label-upper mb-1">Win Rate</div>
            <div className="metric-body text-text-primary">
              {winRate(winningDays, losingDays)}
            </div>
          </div>
          <div>
            <div className="label-upper mb-1">W / L</div>
            <div className="metric-body">
              <span style={{ color: colors.profit }}>{winningDays}</span>
              <span className="text-text-dim mx-1">/</span>
              <span style={{ color: colors.loss }}>{losingDays}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
