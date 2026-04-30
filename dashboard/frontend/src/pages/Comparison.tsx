/**
 * Comparison page — 1v1 head-to-head dry-run experiment.
 *
 * Variant A is the live HYDRA bot (current spread width). Variant B is a
 * parallel HYDRA process running in dry mode with a different spread width.
 * Both see identical market data; the only config delta should be the
 * spread width itself, so we can attribute P&L differences to that lever.
 *
 * Hidden when the backend reports comparison_mode_enabled=false. The nav
 * link gating lives in App.tsx; this page also self-protects by rendering
 * a "disabled" state if accessed directly.
 *
 * Polls /api/variants/comparison every 2s. Single endpoint = one round-trip
 * per refresh, gives us both variants' state + leaderboard + history.
 */
import { useEffect, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Legend,
  ReferenceLine,
  BarChart,
  Bar,
  Cell,
} from "recharts";
import { colors, pnlColor } from "../lib/tradingColors";
import { formatPnL } from "../lib/formatters";

const POLL_MS = 2000;

interface VariantConfig {
  max_spread_width?: number;
  call_starting_otm_multiplier?: number;
  put_starting_otm_multiplier?: number;
  call_stop_buffer?: number;
  put_stop_buffer?: number;
  dry_run?: boolean;
}

interface VariantSummary {
  date?: string;
  state?: string;
  entries_completed: number;
  entries_failed: number;
  entries_skipped: number;
  total_credit_received: number;
  total_realized_pnl: number;
  total_commission: number;
  net_pnl: number;
  call_stops: number;
  put_stops: number;
  total_stops: number;
  active_entries: number;
  total_entries: number;
}

interface BufferInfo {
  call_pct: number | null;
  put_pct: number | null;
  call_value: number | null;
  put_value: number | null;
}

interface VariantEntry {
  entry_number?: number;
  entry_time?: string;
  short_call_strike?: number;
  long_call_strike?: number;
  short_put_strike?: number;
  long_put_strike?: number;
  call_spread_credit?: number;
  put_spread_credit?: number;
  total_credit?: number;
  call_side_stop?: number;
  put_side_stop?: number;
  call_side_stopped?: boolean;
  put_side_stopped?: boolean;
  call_side_expired?: boolean;
  put_side_expired?: boolean;
  call_side_skipped?: boolean;
  put_side_skipped?: boolean;
  is_complete?: boolean;
  buffer?: BufferInfo;
}

interface PnLPoint {
  time: string;
  pnl: number;
}

interface VariantPayload {
  id: string;
  label: string;
  available: boolean;
  reason?: string;
  state_file_age_seconds?: number;
  config: VariantConfig;
  summary?: VariantSummary;
  entries?: VariantEntry[];
  pnl_history?: PnLPoint[];
  peak_buffer?: { call_pct: number; put_pct: number };
  spx_open?: number;
  spx_high?: number;
  spx_low?: number;
  vix_open?: number;
}

interface ComparisonPayload {
  date: string;
  leaderboard: {
    winner: "A" | "B" | "tie" | "n/a";
    a_net_pnl: number;
    b_net_pnl: number;
    delta_net_pnl: number;
  };
  variants: { A: VariantPayload; B: VariantPayload };
}

interface AggregateLifetime {
  cumulative_pnl: number;
  winning_days: number;
  losing_days: number;
  total_credit_collected: number;
  total_stops: number;
  total_entries: number;
  win_rate: number;
  sharpe: number;
  max_drawdown: number;
  best_day: number;
  worst_day: number;
  daily_returns_count?: number;
}

interface CumulativePoint {
  date: string;
  net_pnl: number;
  cumulative: number;
}

interface AggregateVariant {
  label: string;
  lifetime: AggregateLifetime;
  cumulative_curve: CumulativePoint[];
  total_days: number;
}

interface H2HPoint {
  date: string;
  a_net_pnl: number;
  b_net_pnl: number;
  delta: number;
  winner: "A" | "B" | "tie";
  cumulative_a: number;
  cumulative_b: number;
}

interface AggregatePayload {
  variants: { A: AggregateVariant; B: AggregateVariant };
  head_to_head: {
    common_days: number;
    days_a_won: number;
    days_b_won: number;
    days_tied: number;
    cumulative_delta_a_minus_b: number;
    per_day: H2HPoint[];
  };
}

interface Health {
  enabled: boolean;
  variant_a_label: string;
  variant_b_label: string;
}

async function fetchJSON<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

export function Comparison() {
  const [health, setHealth] = useState<Health | null>(null);
  const [data, setData] = useState<ComparisonPayload | null>(null);
  const [aggregate, setAggregate] = useState<AggregatePayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    // Initial health check (also drives the disabled-state UI).
    fetchJSON<Health>("/api/variants/health").then((h) => {
      if (mounted) setHealth(h);
    });

    const tick = async () => {
      const json = await fetchJSON<ComparisonPayload | { detail: string }>(
        "/api/variants/comparison"
      );
      if (!mounted) return;
      if (!json) {
        setError("Network error");
        return;
      }
      if ("detail" in json) {
        setError(json.detail);
        setData(null);
        return;
      }
      setError(null);
      setData(json as ComparisonPayload);
    };

    // Aggregate refreshes much less often — daily-level data only changes at
    // end-of-day (4 PM ET). 30s poll is plenty and keeps the cross-day panel
    // fresh enough for a researcher refreshing during the trading session.
    const aggTick = async () => {
      const json = await fetchJSON<AggregatePayload | { detail: string }>(
        "/api/variants/aggregate"
      );
      if (!mounted) return;
      if (json && !("detail" in json)) {
        setAggregate(json as AggregatePayload);
      }
    };

    tick();
    aggTick();
    const id = setInterval(tick, POLL_MS);
    const aggId = setInterval(aggTick, 30_000);
    return () => {
      mounted = false;
      clearInterval(id);
      clearInterval(aggId);
    };
  }, []);

  if (health && !health.enabled) {
    return <DisabledNotice />;
  }

  if (error && !data) {
    return (
      <div className="p-6 text-text-secondary">
        <h1 className="text-xl text-text-primary mb-2">Comparison</h1>
        <p className="text-sm">{error}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="p-6 text-text-secondary">
        <h1 className="text-xl text-text-primary mb-2">Comparison</h1>
        <p className="text-sm">Loading variant data…</p>
      </div>
    );
  }

  const { variants, leaderboard } = data;
  const a = variants.A;
  const b = variants.B;

  return (
    <div className="space-y-3 px-3 pb-3">
      <Leaderboard
        a={a}
        b={b}
        winner={leaderboard.winner}
        delta={leaderboard.delta_net_pnl}
      />
      <ConfigDelta a={a} b={b} />
      <div className="grid grid-cols-2 gap-3 max-lg:grid-cols-1">
        <VariantPanel v={a} accent={colors.info} />
        <VariantPanel v={b} accent={colors.warning} />
      </div>
      <PnLChart a={a} b={b} />
      <EndOfDayStats a={a} b={b} />
      <CrossDayPanel agg={aggregate} />
    </div>
  );
}

function DisabledNotice() {
  return (
    <div className="p-6 max-w-2xl mx-auto text-text-secondary">
      <h1 className="text-xl text-text-primary mb-3">Comparison Mode</h1>
      <div className="rounded border border-border-dim bg-card p-4 text-sm space-y-2">
        <p className="text-text-primary">Comparison mode is currently disabled.</p>
        <p>
          Enable by setting <code className="text-info">DASHBOARD_COMPARISON_MODE_ENABLED=true</code>{" "}
          in the dashboard environment and restarting <code>dashboard.service</code>.
        </p>
        <p>
          Then start the variant B bot with{" "}
          <code className="text-info">sudo systemctl start hydra_variant_b</code>.
        </p>
      </div>
    </div>
  );
}

function Leaderboard({
  a,
  b,
  winner,
  delta,
}: {
  a: VariantPayload;
  b: VariantPayload;
  winner: string;
  delta: number;
}) {
  const aNet = a.summary?.net_pnl ?? 0;
  const bNet = b.summary?.net_pnl ?? 0;
  const winnerLabel =
    winner === "A" ? a.label : winner === "B" ? b.label : winner === "tie" ? "Tied" : "—";
  const winnerColor =
    winner === "A" ? colors.info : winner === "B" ? colors.warning : colors.textSecondary;

  return (
    <div className="rounded border border-border-dim bg-card p-4">
      <div className="flex items-baseline justify-between gap-4 max-md:flex-col max-md:items-start">
        <div>
          <div className="text-xs uppercase tracking-wide text-text-secondary">
            Today's Leader
          </div>
          <div className="text-2xl font-semibold mt-1" style={{ color: winnerColor }}>
            {winnerLabel}
          </div>
        </div>
        <div className="flex gap-6 max-md:gap-3">
          <PnLBlock label={a.label} value={aNet} accent={colors.info} />
          <PnLBlock label="Δ A − B" value={delta} accent={colors.textSecondary} />
          <PnLBlock label={b.label} value={bNet} accent={colors.warning} />
        </div>
      </div>
    </div>
  );
}

function PnLBlock({ label, value, accent }: { label: string; value: number; accent: string }) {
  return (
    <div className="text-right max-md:text-left">
      <div className="text-[10px] uppercase tracking-wide" style={{ color: accent }}>
        {label}
      </div>
      <div className="text-xl font-mono mt-0.5" style={{ color: pnlColor(value) }}>
        {formatPnL(value)}
      </div>
    </div>
  );
}

function ConfigDelta({ a, b }: { a: VariantPayload; b: VariantPayload }) {
  // Surface the actual config differences so a viewer who lands on the page
  // immediately sees what's being tested. Variant A's config is the
  // "control"; bold rows where B differs.
  const rows: Array<{ key: keyof VariantConfig; label: string }> = [
    { key: "max_spread_width", label: "Spread width (pt)" },
    { key: "call_starting_otm_multiplier", label: "Call start ×" },
    { key: "put_starting_otm_multiplier", label: "Put start ×" },
    { key: "call_stop_buffer", label: "Call stop buffer ($)" },
    { key: "put_stop_buffer", label: "Put stop buffer ($)" },
    { key: "dry_run", label: "Dry-run" },
  ];

  return (
    <div className="rounded border border-border-dim bg-card p-3">
      <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">
        Config Delta
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-text-secondary text-xs">
            <th className="text-left font-normal pb-1">Knob</th>
            <th className="text-right font-normal pb-1" style={{ color: colors.info }}>
              {a.label}
            </th>
            <th className="text-right font-normal pb-1" style={{ color: colors.warning }}>
              {b.label}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ key, label }) => {
            const av = (a.config as Record<string, unknown>)[key as string];
            const bv = (b.config as Record<string, unknown>)[key as string];
            const differs = JSON.stringify(av) !== JSON.stringify(bv);
            return (
              <tr key={key} className={differs ? "text-text-primary" : ""}>
                <td className="py-0.5">{label}</td>
                <td className="py-0.5 text-right font-mono">{String(av ?? "—")}</td>
                <td
                  className="py-0.5 text-right font-mono"
                  style={{ color: differs ? colors.warning : undefined }}
                >
                  {String(bv ?? "—")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function VariantPanel({ v, accent }: { v: VariantPayload; accent: string }) {
  if (!v.available) {
    return (
      <div className="rounded border border-border-dim bg-card p-4">
        <div className="text-xs uppercase tracking-wide" style={{ color: accent }}>
          {v.label}
        </div>
        <div className="mt-2 text-text-secondary text-sm">
          Variant not running.
        </div>
        {v.reason && <div className="text-xs text-text-dim mt-1">{v.reason}</div>}
      </div>
    );
  }

  const summary = v.summary!;
  const entries = v.entries ?? [];
  const peak = v.peak_buffer ?? { call_pct: 0, put_pct: 0 };

  return (
    <div className="rounded border border-border-dim bg-card p-4 space-y-3">
      <div className="flex items-baseline justify-between">
        <div className="text-xs uppercase tracking-wide" style={{ color: accent }}>
          {v.label}
        </div>
        <div className="text-xs text-text-secondary">
          {summary.entries_completed} entries • {summary.total_stops} stops
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-sm">
        <Metric label="Net P&L" value={formatPnL(summary.net_pnl)} colored={summary.net_pnl} />
        <Metric label="Credit" value={`$${summary.total_credit_received.toFixed(0)}`} />
        <Metric label="Commission" value={`$${summary.total_commission.toFixed(0)}`} />
      </div>

      {entries.length === 0 ? (
        <div className="text-sm text-text-dim italic">No entries yet today.</div>
      ) : (
        <div className="space-y-2">
          {entries.map((e, i) => (
            <EntryRow key={i} entry={e} accent={accent} />
          ))}
        </div>
      )}

      <div className="border-t border-border-dim pt-2">
        <div className="text-xs text-text-secondary mb-1">Peak Buffer Use Today</div>
        <div className="flex gap-4 text-xs">
          <BufferBar label="Call" pct={peak.call_pct} />
          <BufferBar label="Put" pct={peak.put_pct} />
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value, colored }: { label: string; value: string; colored?: number }) {
  const color = colored !== undefined ? pnlColor(colored) : colors.textPrimary;
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-text-secondary">{label}</div>
      <div className="text-base font-mono mt-0.5" style={{ color }}>
        {value}
      </div>
    </div>
  );
}

function EntryRow({ entry, accent }: { entry: VariantEntry; accent: string }) {
  const num = entry.entry_number ?? "?";
  const callDone = entry.call_side_stopped || entry.call_side_expired || entry.call_side_skipped;
  const putDone = entry.put_side_stopped || entry.put_side_expired || entry.put_side_skipped;
  const status = entry.is_complete ? "DONE" : "LIVE";
  const statusColor = entry.is_complete ? colors.textDim : accent;

  const buffer = entry.buffer ?? { call_pct: null, put_pct: null, call_value: null, put_value: null };

  return (
    <div className="rounded border border-border-dim bg-bg p-2 text-xs">
      <div className="flex items-center justify-between mb-1">
        <div className="font-mono">
          <span style={{ color: accent }}>#{num}</span>{" "}
          <span className="text-text-secondary">{entry.entry_time?.slice(11, 16) ?? "—"}</span>
        </div>
        <div className="text-[10px] font-mono uppercase tracking-wider" style={{ color: statusColor }}>
          {status}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 font-mono text-text-secondary">
        <div>
          <span className="text-text-dim">C:</span>{" "}
          <span style={{ color: callDone ? colors.textDim : colors.textPrimary }}>
            {entry.short_call_strike}/{entry.long_call_strike}
          </span>
          <span className="text-text-dim ml-2">${entry.call_spread_credit?.toFixed(0)}</span>
        </div>
        <div>
          <span className="text-text-dim">P:</span>{" "}
          <span style={{ color: putDone ? colors.textDim : colors.textPrimary }}>
            {entry.short_put_strike}/{entry.long_put_strike}
          </span>
          <span className="text-text-dim ml-2">${entry.put_spread_credit?.toFixed(0)}</span>
        </div>
      </div>
      {!entry.is_complete && (buffer.call_pct !== null || buffer.put_pct !== null) && (
        <div className="flex gap-3 mt-1.5">
          {buffer.call_pct !== null && (
            <div className="flex-1">
              <div className="text-[10px] text-text-dim">
                C buffer ${buffer.call_value?.toFixed(0)} / stop ${entry.call_side_stop?.toFixed(0)}
              </div>
              <BufferBar pct={buffer.call_pct} compact />
            </div>
          )}
          {buffer.put_pct !== null && (
            <div className="flex-1">
              <div className="text-[10px] text-text-dim">
                P buffer ${buffer.put_value?.toFixed(0)} / stop ${entry.put_side_stop?.toFixed(0)}
              </div>
              <BufferBar pct={buffer.put_pct} compact />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function BufferBar({
  pct,
  label,
  compact = false,
}: {
  pct: number | null;
  label?: string;
  compact?: boolean;
}) {
  const v = pct ?? 0;
  // Buffer usage color: green low, amber mid, red high (inverted vs cushion).
  const color =
    v >= 80 ? colors.loss : v >= 60 ? "#f0883e" : v >= 40 ? colors.warning : colors.profit;
  const height = compact ? "h-1" : "h-1.5";

  return (
    <div className={compact ? "" : "flex-1"}>
      {label && (
        <div className="flex justify-between text-[10px] mb-0.5">
          <span className="text-text-secondary">{label}</span>
          <span className="font-mono" style={{ color }}>
            {pct === null ? "—" : `${v.toFixed(0)}%`}
          </span>
        </div>
      )}
      <div className={`${height} bg-bg-elevated rounded-full overflow-hidden`}>
        <div
          className={`${height} rounded-full transition-all duration-500`}
          style={{ width: `${v}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}

function PnLChart({ a, b }: { a: VariantPayload; b: VariantPayload }) {
  // Merge both variants' P&L history onto a single time axis. Each variant's
  // pnl_history is a list of {time, pnl} written by the bot every ~10s during
  // market hours. We zip them by index because they share the same heartbeat
  // cadence and start time. If one has fewer points (e.g. variant B started
  // late), the missing side is left null — Recharts just skips that point.
  const aHist = a.pnl_history ?? [];
  const bHist = b.pnl_history ?? [];
  const maxLen = Math.max(aHist.length, bHist.length);

  if (maxLen === 0) {
    return (
      <div className="rounded border border-border-dim bg-card p-4">
        <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">
          P&amp;L Over Time
        </div>
        <div className="text-sm text-text-dim italic">
          No P&amp;L data yet — chart will appear once both variants have been
          monitoring an entry for one heartbeat cycle.
        </div>
      </div>
    );
  }

  const merged: { time: string; a: number | null; b: number | null }[] = [];
  for (let i = 0; i < maxLen; i++) {
    merged.push({
      time: (aHist[i] ?? bHist[i])?.time ?? "",
      a: aHist[i]?.pnl ?? null,
      b: bHist[i]?.pnl ?? null,
    });
  }

  return (
    <div className="rounded border border-border-dim bg-card p-3">
      <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">
        P&amp;L Over Time
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={merged} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={colors.borderDim} strokeDasharray="3 3" />
          <XAxis
            dataKey="time"
            stroke={colors.textSecondary}
            tick={{ fontSize: 10 }}
            interval="preserveStartEnd"
          />
          <YAxis
            stroke={colors.textSecondary}
            tick={{ fontSize: 10 }}
            tickFormatter={(v) => `$${v}`}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: colors.bgElevated,
              border: `1px solid ${colors.border}`,
              borderRadius: 4,
              fontSize: 12,
            }}
            formatter={(value, name) => {
              const label = name === "a" ? a.label : b.label;
              if (value === null || value === undefined) return ["—", label];
              return [formatPnL(Number(value)), label];
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11 }}
            formatter={(v) => (v === "a" ? a.label : b.label)}
          />
          <ReferenceLine y={0} stroke={colors.border} />
          <Line
            type="monotone"
            dataKey="a"
            stroke={colors.info}
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="b"
            stroke={colors.warning}
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function EndOfDayStats({ a, b }: { a: VariantPayload; b: VariantPayload }) {
  // Per-variant single-day stats. After 4 PM these become the locked
  // end-of-day comparison; before that they update live.
  const rows: Array<[string, (v: VariantPayload) => string | number, boolean?]> = [
    ["Entries placed", (v) => v.summary?.entries_completed ?? 0],
    ["Stops fired", (v) => v.summary?.total_stops ?? 0],
    ["Total credit", (v) => `$${(v.summary?.total_credit_received ?? 0).toFixed(0)}`],
    ["Realized P&L", (v) => formatPnL(v.summary?.total_realized_pnl ?? 0), true],
    ["Commission", (v) => `$${(v.summary?.total_commission ?? 0).toFixed(0)}`],
    ["Net P&L", (v) => formatPnL(v.summary?.net_pnl ?? 0), true],
    ["Peak call buffer used", (v) => `${(v.peak_buffer?.call_pct ?? 0).toFixed(0)}%`],
    ["Peak put buffer used", (v) => `${(v.peak_buffer?.put_pct ?? 0).toFixed(0)}%`],
  ];

  return (
    <div className="rounded border border-border-dim bg-card p-3">
      <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">
        Day Summary (Live)
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-text-secondary text-xs">
            <th className="text-left font-normal pb-1">Metric</th>
            <th className="text-right font-normal pb-1" style={{ color: colors.info }}>
              {a.label}
            </th>
            <th className="text-right font-normal pb-1" style={{ color: colors.warning }}>
              {b.label}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, getter, colored], i) => {
            const av = getter(a);
            const bv = getter(b);
            const aNum = colored && a.summary ? (a.summary.net_pnl ?? 0) : 0;
            const bNum = colored && b.summary ? (b.summary.net_pnl ?? 0) : 0;
            return (
              <tr key={i}>
                <td className="py-0.5 text-text-secondary">{label}</td>
                <td
                  className="py-0.5 text-right font-mono"
                  style={{ color: colored ? pnlColor(aNum) : undefined }}
                >
                  {av}
                </td>
                <td
                  className="py-0.5 text-right font-mono"
                  style={{ color: colored ? pnlColor(bNum) : undefined }}
                >
                  {bv}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CrossDayPanel({ agg }: { agg: AggregatePayload | null }) {
  // Cross-day rollups are most informative once both variants have at least
  // 2 common days. Below that we render a placeholder explaining what the
  // panel will show, so empty days don't make it look broken.
  if (!agg) {
    return (
      <div className="rounded border border-border-dim bg-card p-3">
        <div className="text-xs uppercase tracking-wide text-text-secondary mb-2">
          Cross-Day Performance
        </div>
        <div className="text-sm text-text-dim italic">Loading…</div>
      </div>
    );
  }

  const a = agg.variants.A;
  const b = agg.variants.B;
  const h2h = agg.head_to_head;
  const hasH2H = h2h.common_days >= 2;

  return (
    <div className="rounded border border-border-dim bg-card p-3 space-y-4">
      <div className="flex items-baseline justify-between">
        <div className="text-xs uppercase tracking-wide text-text-secondary">
          Cross-Day Performance
        </div>
        <div className="text-[11px] text-text-dim">
          A history: {a.total_days}d · B history: {b.total_days}d · H2H window: {h2h.common_days}d
        </div>
      </div>

      {/* Lifetime stats table — always shown */}
      <LifetimeStatsTable a={a} b={b} h2h={h2h} />

      {/* H2H per-day section — only meaningful with 2+ common days */}
      {hasH2H ? (
        <>
          <H2HPerDayChart h2h={h2h} aLabel={a.label} bLabel={b.label} />
          <H2HDeltaBars h2h={h2h} aLabel={a.label} bLabel={b.label} />
        </>
      ) : (
        <div className="rounded border border-border-dim bg-bg p-3 text-xs text-text-dim italic">
          Head-to-head charts will appear after both variants have run for 2+
          common trading days. Currently {h2h.common_days} day
          {h2h.common_days === 1 ? "" : "s"} of overlap.
        </div>
      )}
    </div>
  );
}

function LifetimeStatsTable({
  a,
  b,
  h2h,
}: {
  a: AggregateVariant;
  b: AggregateVariant;
  h2h: AggregatePayload["head_to_head"];
}) {
  // Variant A's history goes back further than B's, so its lifetime stats
  // include data from before the experiment started. We label the column
  // headers with the day count so the user knows the comparison isn't on
  // identical N — the H2H window section below is the apples-to-apples view.
  const rows: Array<[string, (lt: AggregateLifetime) => string, boolean?]> = [
    ["Cumulative P&L", (lt) => formatPnL(lt.cumulative_pnl), true],
    ["Win rate", (lt) => `${(lt.win_rate * 100).toFixed(1)}%`],
    [
      "Win / Loss days",
      (lt) => `${lt.winning_days} / ${lt.losing_days}`,
    ],
    ["Best day", (lt) => formatPnL(lt.best_day), true],
    ["Worst day", (lt) => formatPnL(lt.worst_day), true],
    ["Max drawdown", (lt) => `$${lt.max_drawdown.toFixed(0)}`],
    ["Sharpe (daily)", (lt) => lt.sharpe.toFixed(2)],
    ["Total credit", (lt) => `$${lt.total_credit_collected.toFixed(0)}`],
    ["Total stops", (lt) => `${lt.total_stops}`],
  ];

  return (
    <div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-text-secondary text-xs">
            <th className="text-left font-normal pb-1">Lifetime metric</th>
            <th className="text-right font-normal pb-1" style={{ color: colors.info }}>
              {a.label} ({a.total_days}d)
            </th>
            <th className="text-right font-normal pb-1" style={{ color: colors.warning }}>
              {b.label} ({b.total_days}d)
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, fn, colored], i) => {
            const aVal = fn(a.lifetime);
            const bVal = fn(b.lifetime);
            const aColor =
              colored && a.lifetime.cumulative_pnl !== undefined
                ? pnlColor(a.lifetime[label.includes("Best") ? "best_day" : label.includes("Worst") ? "worst_day" : "cumulative_pnl"])
                : undefined;
            const bColor =
              colored && b.lifetime.cumulative_pnl !== undefined
                ? pnlColor(b.lifetime[label.includes("Best") ? "best_day" : label.includes("Worst") ? "worst_day" : "cumulative_pnl"])
                : undefined;
            return (
              <tr key={i}>
                <td className="py-0.5 text-text-secondary">{label}</td>
                <td className="py-0.5 text-right font-mono" style={{ color: aColor }}>
                  {aVal}
                </td>
                <td className="py-0.5 text-right font-mono" style={{ color: bColor }}>
                  {bVal}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {h2h.common_days > 0 && (
        <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
          <div className="rounded bg-bg p-2 text-center">
            <div className="text-text-dim text-[10px] uppercase tracking-wide">A days won</div>
            <div className="font-mono text-base mt-0.5" style={{ color: colors.info }}>
              {h2h.days_a_won}
            </div>
          </div>
          <div className="rounded bg-bg p-2 text-center">
            <div className="text-text-dim text-[10px] uppercase tracking-wide">Tied</div>
            <div className="font-mono text-base mt-0.5 text-text-secondary">
              {h2h.days_tied}
            </div>
          </div>
          <div className="rounded bg-bg p-2 text-center">
            <div className="text-text-dim text-[10px] uppercase tracking-wide">B days won</div>
            <div className="font-mono text-base mt-0.5" style={{ color: colors.warning }}>
              {h2h.days_b_won}
            </div>
          </div>
        </div>
      )}

      {h2h.common_days > 0 && (
        <div className="mt-2 text-xs text-text-secondary text-center">
          H2H cumulative delta (A − B):{" "}
          <span
            className="font-mono"
            style={{ color: pnlColor(h2h.cumulative_delta_a_minus_b) }}
          >
            {formatPnL(h2h.cumulative_delta_a_minus_b)}
          </span>
        </div>
      )}
    </div>
  );
}

function H2HPerDayChart({
  h2h,
  aLabel,
  bLabel,
}: {
  h2h: AggregatePayload["head_to_head"];
  aLabel: string;
  bLabel: string;
}) {
  // Cumulative P&L curves over the H2H window. We chart `cumulative_a` /
  // `cumulative_b` which are server-computed running sums starting from the
  // first common day — NOT each variant's lifetime cumulative. This way the
  // two lines start at $0 on the same day and divergence is purely the
  // experiment's contribution.
  return (
    <div>
      <div className="text-[11px] text-text-secondary mb-1">
        Cumulative P&amp;L (H2H window only — starts at $0 on first common day)
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={h2h.per_day} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={colors.borderDim} strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            stroke={colors.textSecondary}
            tick={{ fontSize: 10 }}
            interval="preserveStartEnd"
          />
          <YAxis
            stroke={colors.textSecondary}
            tick={{ fontSize: 10 }}
            tickFormatter={(v) => `$${v}`}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: colors.bgElevated,
              border: `1px solid ${colors.border}`,
              borderRadius: 4,
              fontSize: 12,
            }}
            formatter={(value, name) => {
              const label = name === "cumulative_a" ? aLabel : bLabel;
              return [formatPnL(Number(value)), label];
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11 }}
            formatter={(v) => (v === "cumulative_a" ? aLabel : bLabel)}
          />
          <ReferenceLine y={0} stroke={colors.border} />
          <Line
            type="monotone"
            dataKey="cumulative_a"
            stroke={colors.info}
            strokeWidth={2}
            dot={{ r: 3 }}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="cumulative_b"
            stroke={colors.warning}
            strokeWidth={2}
            dot={{ r: 3 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function H2HDeltaBars({
  h2h,
  aLabel,
  bLabel,
}: {
  h2h: AggregatePayload["head_to_head"];
  aLabel: string;
  bLabel: string;
}) {
  // One bar per day, signed delta (A − B). Color by sign so the chart reads
  // at a glance: blue bar = A won that day, amber bar = B won. Tooltip
  // shows the actual per-side P&L for context.
  return (
    <div>
      <div className="text-[11px] text-text-secondary mb-1">
        Daily delta (A − B). Blue bar = A won, amber = B won.
      </div>
      <ResponsiveContainer width="100%" height={150}>
        <BarChart data={h2h.per_day} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={colors.borderDim} strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            stroke={colors.textSecondary}
            tick={{ fontSize: 10 }}
            interval="preserveStartEnd"
          />
          <YAxis
            stroke={colors.textSecondary}
            tick={{ fontSize: 10 }}
            tickFormatter={(v) => `$${v}`}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: colors.bgElevated,
              border: `1px solid ${colors.border}`,
              borderRadius: 4,
              fontSize: 12,
            }}
            formatter={(_value, _name, item) => {
              const p = (item?.payload || {}) as H2HPoint;
              return [
                `Δ ${formatPnL(p.delta)}  ·  ${aLabel} ${formatPnL(p.a_net_pnl)}  ·  ${bLabel} ${formatPnL(p.b_net_pnl)}`,
                "",
              ];
            }}
          />
          <ReferenceLine y={0} stroke={colors.border} />
          <Bar dataKey="delta" isAnimationActive={false}>
            {h2h.per_day.map((p, i) => (
              <Cell
                key={i}
                fill={
                  p.winner === "A"
                    ? colors.info
                    : p.winner === "B"
                    ? colors.warning
                    : colors.textSecondary
                }
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
