import { useState, useEffect } from "react";
import { useSelectedSnapshotStore } from "../dashboard/selectedSnapshotStore";
import { resolveFomcPolicy } from "../../lib/fomcPolicy";
import { useHydraStore } from "../../store/hydraStore";
import { formatPnL, winRate } from "../../lib/formatters";
import { capitalBasisConfig } from "../../lib/pnlShape";
import { useSelectedStrategy } from "../../hooks/useSelectedStrategy";
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

/** FOMC alert strip — rendered as overlay above any state.
 *  Shows HYDRA-specific behavior for each FOMC phase (current: 2026-04-19):
 *  Day 1: Normal trading (no announcement)
 *  Day 2 (announcement): Normal trading (fomc_announcement_skip=false — A/B showed coin-flip with slight positive edge)
 *  T+1: ALL entries skipped (fomc_t1_skip_enabled=true — supersedes MKT-038 call-only)
 */
export function FOMCBanner() {
  const market = useHydraStore((s) => s.market);
  const hydraState = useHydraStore((s) => s.hydraState);
  // THE SELECTED variant's own policy, not the primary seat's. `hydraState` is
  // the live seat (B), which DOES skip announcement days — while D, E, F, G and
  // H all trade through them. Reading B's flags for every selection meant this
  // banner told the operator that a strategy was flat on exactly the day it was
  // most exposed, and G is an undefined-risk naked strangle.
  const selected = useSelectedSnapshotStore((s) => s.snapshot);
  if (!market) return null;

  const isFomcDay = market.is_fomc_day;
  const isAnnouncement = market.is_fomc_announcement;
  const isTPlus1 = market.is_fomc_t_plus_one;
  const daysUntil = market.days_until_fomc;
  // FOMC T+1 handling (2026-04-19): skip takes precedence over MKT-038 call-only.
  // Defaults assume today's VM config (skip=true, callonly=false) if state not yet populated.
  const effectiveFomc = resolveFomcPolicy(selected?.fomc_policy, hydraState);
  const t1SkipEnabled = effectiveFomc.t1_skip;
  const mkt038Enabled = hydraState?.fomc_t1_callonly_enabled === true;
  // 2026-04-30: when bot is in dry mode AND dry_run_force_normal_day is set,
  // FOMC date-based skips (announce + T+1 + MKT-038) are runtime-bypassed.
  // Banner must show "trading normally (dry-run override)" to reflect actual
  // bot behavior, not the static config flags.
  const forceNormalDay = Boolean(
    hydraState?.dry_run && hydraState?.dry_run_force_normal_day
  );

  // Show on: FOMC day, T+1, or 1-2 days before
  const showApproaching = !isFomcDay && !isTPlus1 && daysUntil != null && daysUntil > 0 && daysUntil <= 2;
  if (!isFomcDay && !isTPlus1 && !showApproaching) return null;

  // Determine headline + HYDRA behavior tag
  let headline: string;
  let hydraTag: string;
  let hydraTagColor: string;

  if (isAnnouncement) {
    headline = "FOMC Announcement Day — Rate Decision at 2:00 PM ET";
    // fomc_announcement_skip defaults false on VM (Day 2 backtest was coin-flip with slight positive edge)
    const day2Skip = effectiveFomc.announcement_skip;
    if (forceNormalDay) {
      hydraTag = "HYDRA: Trading normally — dry-run force-normal-day bypass active";
      hydraTagColor = colors.warning;
    } else {
      hydraTag = day2Skip
        ? "HYDRA: All entries skipped (fomc_announcement_skip)"
        : "HYDRA: Trading normally — Day 2 backtest showed slight positive edge";
      hydraTagColor = day2Skip ? colors.loss : colors.warning;
    }
  } else if (isFomcDay) {
    headline = "FOMC Meeting Day 1 — Announcement Tomorrow at 2:00 PM ET";
    hydraTag = "HYDRA: Normal trading — no announcement today";
    hydraTagColor = colors.profit;
  } else if (isTPlus1) {
    headline = "Post-FOMC Day (T+1) — Elevated Volatility Expected";
    if (forceNormalDay) {
      // Dry-run override bypasses T+1 skip + MKT-038 — bot will trade normally
      // for full data collection (variant comparison experiment).
      hydraTag = "HYDRA: Trading normally — dry-run force-normal-day bypass active";
      hydraTagColor = colors.warning;
    } else if (t1SkipEnabled) {
      hydraTag = "HYDRA: ALL ENTRIES SKIPPED (FOMC T+1 blackout)";
      hydraTagColor = colors.loss;
    } else if (mkt038Enabled) {
      hydraTag = "HYDRA: Call-only entries (MKT-038) — puts skipped";
      hydraTagColor = colors.warning;
    } else {
      hydraTag = "HYDRA: Normal trading (T+1 skip and MKT-038 both disabled)";
      hydraTagColor = colors.textSecondary;
    }
  } else {
    headline = `FOMC Meeting in ${daysUntil} day${daysUntil !== 1 ? "s" : ""}`;
    hydraTag = daysUntil === 1
      ? "HYDRA: Normal trading today — T+1 blackout takes effect day after tomorrow"
      : "HYDRA: Normal trading — FOMC approaching";
    hydraTagColor = colors.textDim;
  }

  return (
    <div
      className="rounded-lg border px-4 py-2.5 flex items-start gap-3"
      style={{
        backgroundColor: isTPlus1
          ? "rgba(210, 153, 34, 0.05)"
          : "rgba(210, 153, 34, 0.08)",
        borderColor: "rgba(210, 153, 34, 0.25)",
      }}
    >
      <AlertTriangle size={16} style={{ color: colors.warning }} className="shrink-0 mt-0.5" />
      <div className="text-xs space-y-1">
        <div style={{ color: colors.warning }} className="font-semibold">
          {headline}
        </div>
        <div style={{ color: hydraTagColor }}>
          {hydraTag}
        </div>
      </div>
    </div>
  );
}

/** Main context banner — shown in compact layout (market closed, no data). */
/**
 * The exact cumulative fields this banner reads. Declared structurally (rather
 * than importing the snapshot type) so the WS store's metrics object and the
 * polled snapshot's `cumulative` block both satisfy it.
 */
interface CumulativeLike {
  cumulative_pnl?: number;
  winning_days?: number;
  losing_days?: number;
  roi_pct?: number;
  avg_capital_per_day?: number | null;
}

/** Best/worst/average day. The WS store holds the LIVE SEAT's only, so any
 *  non-primary view must be given this explicitly or it shows the seat's. */
interface ComparisonsLike {
  best_day?: number | null;
  worst_day?: number | null;
  avg_pnl?: number | null;
}

/**
 * ``cumulative`` overrides the WS store so a NON-primary strategy can render
 * this banner from its own polled snapshot. Omit it on the primary and the
 * store supplies the values, exactly as before.
 */
export function MarketContextBanner({
  cumulative,
  comparisons: comparisonsProp,
}: {
  cumulative?: CumulativeLike;
  comparisons?: ComparisonsLike;
} = {}) {
  const market = useHydraStore((s) => s.market);
  const storeMetrics = useHydraStore((s) => s.metrics);
  const storeComparisons = useHydraStore((s) => s.comparisons);
  // Hooks must run on EVERY render, so this sits ABOVE the early returns below.
  // It used to be called after them, which is a rules-of-hooks violation: the
  // hook count changed when `market` arrived or `is_open` flipped at the open
  // and close. (Benign only because nothing else hooked in between.)
  const { strategy: _sel } = useSelectedStrategy();

  const metrics = (cumulative ?? storeMetrics) as typeof storeMetrics;
  const comparisons = (comparisonsProp ?? storeComparisons) as typeof storeComparisons;

  if (!market) return null;
  if (market.is_open) return null;

  const nextOpen = formatNextOpen(market.next_event?.next_open);

  const cumulativePnl = metrics?.cumulative_pnl ?? 0;
  // Capital labels follow the strategy's CAPITAL BASIS (see DailyPnLCard).
  const _basis = capitalBasisConfig(_sel?.capital_basis);
  const _capitalLabel = _basis?.capitalLabel ?? "Capital / Day";
  const _returnLabel = _basis?.returnLabel ?? "ROI (on capital)";
  const _hasCapital =
    metrics?.avg_capital_per_day != null && metrics.avg_capital_per_day > 0;
  const winningDays = metrics?.winning_days ?? 0;
  const losingDays = metrics?.losing_days ?? 0;
  const totalDays = winningDays + losingDays;
  const avgPerDay = totalDays > 0 ? cumulativePnl / totalDays : 0;
  const roiPct = metrics?.roi_pct ?? 0;
  const avgCapitalPerDay = metrics?.avg_capital_per_day ?? 0;
  const bestDay = comparisons?.best_day;
  const worstDay = comparisons?.worst_day;

  // Determine context
  const isWeekend = !market.is_trading_day && !market.holiday_name;
  const isHoliday = !market.is_trading_day && !!market.holiday_name;
  const isPreMarket = market.is_trading_day && market.session === "pre_market";
  const isPostMarket = market.is_trading_day && !market.is_open;

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
      {/* Badge + subtitle */}
      <div className="flex items-center gap-3 mb-4">
        <Icon size={20} style={{ color: badgeColor }} />
        <span
          className="text-xs font-bold uppercase tracking-widest"
          style={{ color: badgeColor }}
        >
          {badge}
        </span>
      </div>
      <p className="text-sm text-text-secondary mb-4">{subtitle}</p>

      {/* Cumulative stats */}
      {totalDays > 0 && (
        <div className="grid grid-cols-3 gap-4 max-sm:grid-cols-2">
          <div>
            <div className="label-upper mb-1">
              Cumulative P&L
              {metrics?.cumulative_baseline_date && (
                <span className="ml-1 normal-case text-text-dim font-normal">
                  · since {metrics.cumulative_baseline_date}
                </span>
              )}
            </div>
            <div className="metric-body font-bold" style={{ color: pnlColor(cumulativePnl) }}>
              {formatPnL(cumulativePnl)}
            </div>
          </div>
          <div>
            <div className="label-upper mb-1">Avg / Day</div>
            <div className="metric-body" style={{ color: pnlColor(avgPerDay) }}>
              {formatPnL(avgPerDay)}
            </div>
          </div>
          <div>
            <div className="label-upper mb-1">{_returnLabel}</div>
            <div className="metric-body" style={{ color: _hasCapital ? pnlColor(roiPct) : undefined }}>
              {_hasCapital
                ? `${roiPct >= 0 ? "+" : ""}${roiPct.toFixed(2)}%`
                : <span className="text-text-dim">—</span>}
            </div>
          </div>
          <div>
            <div className="label-upper mb-1">{_capitalLabel}</div>
            <div className="metric-body text-text-primary">
              {_hasCapital
                ? `$${Math.round(avgCapitalPerDay).toLocaleString()}`
                : <span className="text-text-dim" title={_basis?.denominator}>—</span>}
            </div>
          </div>
          <div>
            <div className="label-upper mb-1">Win Rate</div>
            <div className="metric-body text-text-primary">
              {winRate(winningDays, losingDays)}
            </div>
          </div>
          <div>
            <div className="label-upper mb-1">Trading Days</div>
            <div className="metric-body text-text-primary">{totalDays}</div>
          </div>
          <div>
            <div className="label-upper mb-1">W / L</div>
            <div className="metric-body">
              <span style={{ color: colors.profit }}>{winningDays}</span>
              <span className="text-text-dim mx-1">/</span>
              <span style={{ color: colors.loss }}>{losingDays}</span>
            </div>
          </div>
          {bestDay != null && worstDay != null && (
            <div>
              <div className="label-upper mb-1">Best / Worst</div>
              <div className="metric-body">
                <span style={{ color: colors.profit }}>{formatPnL(bestDay)}</span>
                <span className="text-text-dim mx-1">/</span>
                <span style={{ color: colors.loss }}>{formatPnL(worstDay)}</span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Daily summary row shape from /api/metrics/daily. */
interface DailySummary {
  date: string;
  net_pnl: number;
  gross_pnl: number;
  commission: number;
  entries_placed: number;
  entries_stopped: number;
  entries_expired: number;
  day_of_week: string;
  spx_open: number;
  spx_close: number;
  day_range: number;
  vix_open: number;
  vix_close: number;
}

/** Format date string as "Fri Mar 13". */
function formatShortDate(dateStr: string): string {
  try {
    const d = new Date(dateStr + "T12:00:00");
    return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  } catch {
    return dateStr;
  }
}

/** Per-entry P&L computed from entries + stops. */
interface EntryPnL {
  entry_number: number;
  total_credit: number;
  pnl: number;
  stopped: boolean;
}

/** Two side-by-side summary cards for off-market display. */
/**
 * ``strategyId`` scopes BOTH fetches below. They previously carried no
 * ``strategy_id`` at all, so this card always showed the live seat's last day
 * regardless of the picked strategy — the same defect class as the 2026-07-14
 * cross-wiring fix (4b3d6a0), surviving in the component layer after the
 * endpoints themselves were scoped.
 */
export function OffDaySummaryCards({
  strategyId,
  comparisons: comparisonsProp,
}: { strategyId?: string; comparisons?: ComparisonsLike } = {}) {
  const storeComparisons = useHydraStore((s) => s.comparisons);
  const comparisons = (comparisonsProp ?? storeComparisons) as typeof storeComparisons;
  const [recentDays, setRecentDays] = useState<DailySummary[]>([]);
  const [lastDayEntries, setLastDayEntries] = useState<EntryPnL[]>([]);
  // EOD auto-update (operator request): these "Last Trading Day" / "Week in
  // Review" widgets are end-of-day fields that previously fetched ONCE on mount
  // and went stale until a manual reload. We re-run the fetch when the trading
  // day ends OR when settlement writes new cumulative metrics — both signals
  // arrive over the live WebSocket (market_status flips is_open→false at 4 PM ET;
  // the metrics poll re-reads hydra_metrics.json ~10s and updates last_updated).
  // Keying the effect on those means the cards refresh at close with no reload.
  const marketOpen = useHydraStore((s) => s.market?.is_open ?? null);
  const metricsUpdated = useHydraStore((s) => s.metrics?.last_updated ?? null);

  const sidParam = strategyId ? `&strategy_id=${strategyId}` : "";

  useEffect(() => {
    fetch(`/api/metrics/daily?days=10${sidParam}`)
      .then((r) => r.json())
      .then((data) => {
        if (data.summaries) {
          setRecentDays(data.summaries);
          // Fetch entries for the most recent day
          if (data.summaries.length > 0) {
            const lastDate = data.summaries[0].date;
            fetch(`/api/hydra/entries?date_str=${lastDate}${sidParam}`)
              .then((r) => r.json())
              .then((eData) => {
                if (eData.entries && eData.entries.length > 0) {
                  const stops: Record<string, number> = {};
                  for (const s of eData.stops ?? []) {
                    const key = `${s.entry_number}_${s.side}`;
                    stops[key] = s.net_pnl ?? 0;
                  }
                  const computed: EntryPnL[] = eData.entries.map((e: Record<string, unknown>) => {
                    const num = e.entry_number as number;
                    const credit = (e.total_credit as number) ?? 0;
                    // Check if any side was stopped
                    const callStop = stops[`${num}_call`];
                    const putStop = stops[`${num}_put`];
                    const wasStopped = callStop !== undefined || putStop !== undefined;
                    // P&L = credit kept from expired sides + stop P&L from stopped sides
                    let pnl = 0;
                    if (callStop !== undefined) pnl += callStop;
                    else pnl += (e.call_credit as number) ?? 0;
                    if (putStop !== undefined) pnl += putStop;
                    else pnl += (e.put_credit as number) ?? 0;
                    return { entry_number: num, total_credit: credit, pnl, stopped: wasStopped };
                  });
                  setLastDayEntries(computed);
                }
              })
              .catch(() => {});
          }
        }
      })
      .catch(() => {});
    // marketOpen / metricsUpdated drive the EOD re-fetch (see note above);
    // sidParam re-fetches when the operator switches strategy.
  }, [marketOpen, metricsUpdated, sidParam]);

  // Last trading day = most recent entry (summaries come DESC from API)
  const lastDay = recentDays.length > 0 ? recentDays[0] : null;

  // Week in review = this calendar week (Mon-Fri)
  // Find the most recent Monday and sum all days from that week
  const weekDays = (() => {
    if (recentDays.length === 0) return [];
    const latest = new Date(recentDays[0].date + "T12:00:00");
    const dayOfWeek = latest.getDay(); // 0=Sun, 1=Mon, ...
    // Find the Monday of the latest day's week
    const mondayOffset = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
    const monday = new Date(latest);
    monday.setDate(monday.getDate() - mondayOffset);
    const mondayStr = monday.toISOString().slice(0, 10);
    return recentDays.filter((d) => d.date >= mondayStr && d.date <= recentDays[0].date);
  })();

  const weekPnl = weekDays.reduce((s, d) => s + d.net_pnl, 0);
  const weekEntries = weekDays.reduce((s, d) => s + d.entries_placed, 0);
  const weekStops = weekDays.reduce((s, d) => s + d.entries_stopped, 0);
  const weekWins = weekDays.filter((d) => d.net_pnl > 0).length;
  const weekLosses = weekDays.filter((d) => d.net_pnl < 0).length;

  const avgPnl = comparisons?.avg_pnl ?? 0;

  return (
    <div className="grid grid-cols-2 gap-3 max-md:grid-cols-1">
      {/* Last Trading Day */}
      <div className="bg-card rounded-lg border border-border-dim p-4">
        <h3 className="label-upper mb-3">
          Last Trading Day
          {lastDay && (
            <span className="text-text-dim font-normal ml-2">
              {formatShortDate(lastDay.date)}
            </span>
          )}
        </h3>
        {lastDay ? (
          <>
            <div className="text-center mb-3">
              <span className="metric-hero" style={{ color: pnlColor(lastDay.net_pnl) }}>
                {formatPnL(lastDay.net_pnl)}
              </span>
              {avgPnl !== 0 && (
                <div className="text-3xs mt-1" style={{ color: lastDay.net_pnl > avgPnl ? colors.profit : colors.loss }}>
                  {lastDay.net_pnl > avgPnl ? "↑" : "↓"} vs {formatPnL(avgPnl)} avg
                </div>
              )}
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="flex justify-between">
                <span className="text-text-secondary">Entries</span>
                <span className="text-text-primary">{lastDay.entries_placed}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-secondary">Stops</span>
                <span style={{ color: lastDay.entries_stopped > 0 ? colors.loss : colors.textPrimary }}>
                  {lastDay.entries_stopped}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-secondary">SPX Range</span>
                <span className="text-text-primary">{(lastDay.day_range ?? 0).toFixed(0)}pt</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-secondary">VIX</span>
                <span className="text-text-primary">{(lastDay.vix_close ?? 0).toFixed(1)}</span>
              </div>
            </div>

            {/* Mini entry breakdown */}
            {lastDayEntries.length > 0 && (
              <div className="mt-3 pt-3 border-t border-border-dim">
                <div className="flex gap-1">
                  {lastDayEntries.map((e) => (
                    <div
                      key={e.entry_number}
                      className="flex-1 text-center rounded py-1"
                      style={{ backgroundColor: e.pnl > 0 ? "rgba(126, 232, 199, 0.1)" : e.pnl < 0 ? "rgba(248, 81, 73, 0.1)" : "transparent" }}
                    >
                      <div className="text-3xs text-text-dim">E{e.entry_number}</div>
                      <div className="text-3xs font-semibold" style={{ color: pnlColor(e.pnl) }}>
                        {(e.pnl ?? 0) > 0 ? "+" : ""}{(e.pnl ?? 0).toFixed(0)}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="text-text-dim text-sm text-center py-4">No data yet</div>
        )}
      </div>

      {/* Week in Review */}
      <div className="bg-card rounded-lg border border-border-dim p-4">
        <h3 className="label-upper mb-3">
          Week in Review
          {weekDays.length > 0 && (
            <span className="text-text-dim font-normal ml-2">
              {weekDays.length} day{weekDays.length !== 1 ? "s" : ""}
            </span>
          )}
        </h3>
        {weekDays.length > 0 ? (
          <>
            <div className="text-center mb-3">
              <span className="metric-hero" style={{ color: pnlColor(weekPnl) }}>
                {formatPnL(weekPnl)}
              </span>
              {weekDays.length > 0 && (
                <div className="text-3xs mt-1 text-text-dim">
                  {formatPnL(weekPnl / weekDays.length)} avg/day
                </div>
              )}
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="flex justify-between">
                <span className="text-text-secondary">W / L</span>
                <span>
                  <span style={{ color: colors.profit }}>{weekWins}</span>
                  <span className="text-text-dim"> / </span>
                  <span style={{ color: colors.loss }}>{weekLosses}</span>
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-secondary">Entries</span>
                <span className="text-text-primary">{weekEntries}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-secondary">Stops</span>
                <span style={{ color: weekStops > 0 ? colors.loss : colors.textPrimary }}>
                  {weekStops}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-secondary">Win Rate</span>
                <span className="text-text-primary">
                  {winRate(weekWins, weekLosses)}
                </span>
              </div>
            </div>

            {/* Mini daily breakdown */}
            <div className="mt-3 pt-3 border-t border-border-dim">
              <div className="flex gap-1">
                {weekDays.slice().reverse().map((d) => (
                  <div
                    key={d.date}
                    className="flex-1 text-center rounded py-1"
                    style={{ backgroundColor: d.net_pnl > 0 ? "rgba(126, 232, 199, 0.1)" : d.net_pnl < 0 ? "rgba(248, 81, 73, 0.1)" : "transparent" }}
                  >
                    <div className="text-3xs text-text-dim">{d.day_of_week.slice(0, 3)}</div>
                    <div className="text-3xs font-semibold" style={{ color: pnlColor(d.net_pnl) }}>
                      {(d.net_pnl ?? 0) > 0 ? "+" : ""}{(d.net_pnl ?? 0).toFixed(0)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </>
        ) : (
          <div className="text-text-dim text-sm text-center py-4">No data yet</div>
        )}
      </div>
    </div>
  );
}
