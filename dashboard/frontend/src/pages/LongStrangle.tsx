/**
 * Strategy H — 0DTE Long Strangle. Its own page, not a row in a comparison.
 *
 * WHY THIS IS NOT ONE OF THE EXISTING VIEWS
 * ------------------------------------------
 * Every other renderer on this dashboard assumes premium was COLLECTED. "Expired
 * worthless" is the best outcome there and the WORST one here; P&L is a
 * percentage of a credit there and of a DEBIT here; capital is a spread width
 * there and there is no spread at all here. Folding H into the iron-condor view
 * would not merely look odd — it would state its numbers backwards.
 *
 * WHAT THIS PAGE SHOWS THAT NO OTHER ONE DOES
 * --------------------------------------------
 * 1. **Max loss as a fact, not an estimate.** It is the debit paid, known before
 *    the position opens. No other strategy here can say that about its own day.
 * 2. **The peak next to the exit.** A long strangle can touch +50% on a gamma
 *    spike and give it all back inside a minute, so what a position REACHED and
 *    what it CAPTURED are different numbers — and the gap between them is the
 *    measurement this variant exists to produce. It is rendered as a first-class
 *    column, not left for someone to derive.
 * 3. **Declined entries with their counterfactual.** Proposed strikes, debit and
 *    expected move for every skip, so a veto can be scored later. The GEX work on
 *    variant B had to be retro-fitted for this and could never recover its first
 *    95 vetoes.
 *
 * Everything here is dry-run — H is dry-run LOCKED (it refuses to construct
 * outside dry-run) and places no real orders. It was installed on the VM on
 * 2026-09-23 and began recording the same morning, so `available: true` is the
 * normal response now; the `available: false` branch remains for the window
 * before a variant's first row and says so plainly rather than rendering an
 * empty grid.
 */

import { useEffect, useState } from "react";
import { AlertTriangle, TrendingUp } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCurrency, formatPnL } from "../lib/formatters";
import {
  bandVerdict,
  sessionIsSettled,
  todayInNewYork,
  valueStrangleAt,
} from "../lib/strangleVerdict";

interface Mark {
  timestamp: string;
  spx: number | null;
  total_value: number | null;
  unrealized_pnl: number | null;
  pnl_pct_of_debit: number | null;
}

interface Position {
  entry_number: number;
  entry_time: string | null;
  call_strike: number | null;
  put_strike: number | null;
  contracts: number | null;
  total_debit: number | null;
  spx_at_entry: number | null;
  vix_at_entry: number | null;
  em_source: string | null;
  expected_move: number | null;
  skew_gap_pct: number | null;
  peak_pct: number | null;
  trough_pct: number | null;
  last_mark?: Mark | null;
  exit_time?: string | null;
  exit_reason?: string | null;
  realized_pnl?: number | null;
  pnl_pct_of_debit?: number | null;
  minutes_held?: number | null;
  peak_minus_exit_pct?: number | null;
}

interface Skip {
  entry_number: number;
  skip_time: string | null;
  skip_reason: string | null;
  spx: number | null;
  proposed_call_strike: number | null;
  proposed_put_strike: number | null;
  proposed_debit: number | null;
  em_source: string | null;
  expected_move: number | null;
}

interface RecentRow {
  date: string;
  entry_number: number;
  exit_reason: string | null;
  realized_pnl: number | null;
  pnl_pct_of_debit: number | null;
  minutes_held: number | null;
  total_debit: number | null;
  contracts: number | null;
  em_source: string | null;
}

interface SpxPoint {
  t: string;
  spx: number;
}

interface Status {
  strategy: string;
  label: string;
  available: boolean;
  reason?: string;
  date: string;
  open: Position[];
  closed: Position[];
  skipped: Skip[];
  spx_path?: SpxPoint[];
  summary: {
    open_count: number;
    closed_count: number;
    skipped_count: number;
    debit_deployed: number;
    realized_pnl: number;
    max_possible_loss: number;
  };
}

const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;

const money = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : formatCurrency(v, 0);

/**
 * THE picture a long strangle needs, and the one thing no other view here
 * provides: SPX's path against the expected-move band it was priced at.
 *
 * Every H session reduces to one question — **did it move enough?** — and the
 * answer is a glance, not a calculation. It works even on a day H DECLINED to
 * enter, because the skip row carries the strikes it would have used. Without
 * it the "Declined" list reports that a veto happened and says nothing about
 * whether the veto was right, which is exactly the counterfactual the variant
 * exists to collect.
 */
function ExpectedMoveBand({
  path,
  callStrike,
  putStrike,
  entrySpx,
  em,
  hypothetical,
  debit,
  sessionDate,
}: {
  path: SpxPoint[];
  callStrike: number | null;
  putStrike: number | null;
  entrySpx: number | null;
  em: number | null;
  hypothetical: boolean;
  debit: number | null;
  sessionDate: string;
}) {
  if (path.length < 2 || !callStrike || !putStrike) return null;

  const prices = path.map((p) => p.spx);
  const hi = Math.max(...prices);
  const lo = Math.min(...prices);
  // Pad so the band is never flush against the frame, and so a breach reads as
  // a breach rather than as the line touching the edge of the chart.
  const pad = Math.max(8, (callStrike - putStrike) * 0.25);
  const yMin = Math.min(lo, putStrike) - pad;
  const yMax = Math.max(hi, callStrike) + pad;

  // Both sides can break on the SAME session, so the verdict leads with the one
  // that moved furthest. See lib/strangleVerdict.ts for why that matters.
  const v = bandVerdict(hi, lo, callStrike, putStrike);

  // What it would actually have been worth. For a DECLINED entry this is the
  // whole point: "a veto happened" is not a result, "the veto cost $831" is.
  // SPXW is cash-settled at the close, so intrinsic at the last print is the
  // FINAL number only once the close has passed — before it, the position is
  // still worth intrinsic plus whatever time value remains, and the sentence
  // below says so rather than calling a mid-session mark a settlement.
  const last = path[path.length - 1];
  const settled = sessionIsSettled(sessionDate, todayInNewYork(), last.t);
  const val = valueStrangleAt(last.spx, callStrike, putStrike, debit, settled);

  return (
    <section>
      <div className="flex flex-wrap items-baseline justify-between gap-2 mb-1.5">
        <h2 className="text-xs font-semibold text-text-primary">
          Did it move enough?
        </h2>
        <div className="text-3xs text-text-dim">
          band = spot {entrySpx?.toFixed(0) ?? "?"} ± {em?.toFixed(1) ?? "?"}pt
          {hypothetical && " · position was DECLINED — this is the counterfactual"}
        </div>
      </div>

      <div className="bg-bg-elevated border border-border-dim rounded p-3">
        <div style={{ width: "100%", height: 220 }}>
          <ResponsiveContainer>
            <LineChart data={path} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="2 4" stroke="var(--color-border-dim)" />
              {/* Inside the band is where a long strangle LOSES — shaded as the
                  losing region, which is the opposite of every other chart on
                  this dashboard and the point worth making visually. */}
              <ReferenceArea
                y1={putStrike}
                y2={callStrike}
                fill="var(--color-loss)"
                fillOpacity={0.07}
              />
              <ReferenceLine
                y={callStrike}
                stroke="var(--color-profit)"
                strokeDasharray="4 3"
                label={{
                  value: `C ${callStrike.toFixed(0)}`,
                  position: "insideTopRight",
                  fontSize: 10,
                  fill: "var(--color-text-dim)",
                }}
              />
              <ReferenceLine
                y={putStrike}
                stroke="var(--color-profit)"
                strokeDasharray="4 3"
                label={{
                  value: `P ${putStrike.toFixed(0)}`,
                  position: "insideBottomRight",
                  fontSize: 10,
                  fill: "var(--color-text-dim)",
                }}
              />
              <XAxis
                dataKey="t"
                tick={{ fontSize: 10, fill: "var(--color-text-dim)" }}
                interval="preserveStartEnd"
                minTickGap={48}
              />
              <YAxis
                domain={[yMin, yMax]}
                tick={{ fontSize: 10, fill: "var(--color-text-dim)" }}
                width={52}
                tickFormatter={(v: number) => v.toFixed(0)}
              />
              <Tooltip
                contentStyle={{
                  background: "var(--color-bg-elevated)",
                  border: "1px solid var(--color-border-dim)",
                  borderRadius: 4,
                  fontSize: 11,
                }}
                formatter={(v: number | undefined) => [v?.toFixed(2) ?? "—", "SPX"]}
              />
              <Line
                type="monotone"
                dataKey="spx"
                stroke="var(--color-text-primary)"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="mt-2 text-xs space-y-1">
          <div>
            {v.broke ? (
              <span className="text-profit font-medium">
                Broke the {v.widestSide} side by {v.widest.toFixed(2)}pt
                {v.brokeCall && v.brokePut && (
                  <span className="font-normal text-text-secondary">
                    {" "}
                    (both sides broke — {v.otherSide} by{" "}
                    {v.otherThrough.toFixed(2)}pt)
                  </span>
                )}
              </span>
            ) : (
              <span className="text-text-secondary">
                {settled
                  ? "Stayed inside the band all session — the debit was lost in full"
                  : "Inside the band so far"}{" "}
                — closest approach {v.closestApproach.toFixed(2)}pt from the{" "}
                {v.widestSide} strike
              </span>
            )}
            <span className="text-text-dim">
              {"  ·  "}session range {lo.toFixed(2)} – {hi.toFixed(2)}
            </span>
          </div>

          {/* THE result, not just the path. Shown only for a declined entry:
              a real position has an actual exit and does not need a
              counterfactual. */}
          {hypothetical && debit != null && debit > 0 && (
            <div className={val.pnl >= 0 ? "text-profit" : "text-loss"}>
              {val.settled ? (
                <>
                  Settled at {last.spx.toFixed(2)} ({last.t}) worth{" "}
                  {formatCurrency(val.intrinsic, 0)} against a{" "}
                  {formatCurrency(debit, 0)} debit —{" "}
                  <span className="font-semibold">
                    {formatPnL(val.pnl, 0)} ({val.pctOfDebit?.toFixed(0)}%)
                  </span>
                  {val.pnl > 0 && " that declining the entry gave up"}
                </>
              ) : (
                <>
                  At the last print ({last.t}, {last.spx.toFixed(2)}) the legs hold{" "}
                  {formatCurrency(val.intrinsic, 0)} of intrinsic value against a{" "}
                  {formatCurrency(debit, 0)} debit —{" "}
                  <span className="font-semibold">
                    {formatPnL(val.pnl, 0)} ({val.pctOfDebit?.toFixed(0)}%)
                  </span>
                  <span className="text-text-dim">
                    {" "}
                    · intrinsic only — the session is still open, so the position
                    is worth this plus its remaining time value
                  </span>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function Card({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="bg-bg-elevated rounded p-3 border border-border-dim">
      <div className="text-3xs uppercase tracking-wider text-text-dim">{label}</div>
      <div className="text-lg font-semibold text-text-primary mt-0.5">{value}</div>
      {hint && <div className="text-3xs text-text-dim mt-0.5">{hint}</div>}
    </div>
  );
}

/**
 * The running record across days — and the one summary that decides whether
 * this strategy is what its source claims.
 *
 * The source claims ~80% winners at +50-100% against losers at -100%. Two
 * numbers test that and neither is visible in a daily view: the WIN RATE over
 * the whole sample, and the AVERAGE RETURN ON THE DEBIT. They are rendered with
 * the sample size attached, because a win rate without an `n` is not a
 * measurement.
 */
function RunningRecord({ rows }: { rows: RecentRow[] }) {
  const scored = rows.filter((r) => r.realized_pnl !== null);
  if (scored.length === 0) return null;
  const wins = scored.filter((r) => (r.realized_pnl ?? 0) > 0).length;
  const net = scored.reduce((a, r) => a + (r.realized_pnl ?? 0), 0);
  const risked = scored.reduce((a, r) => a + (r.total_debit ?? 0), 0);
  const avgPct =
    scored.reduce((a, r) => a + (r.pnl_pct_of_debit ?? 0), 0) / scored.length;

  return (
    <section>
      <h2 className="text-xs font-semibold text-text-primary mb-1.5">
        Running record
      </h2>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2">
        <Card
          label="Win rate"
          value={`${((wins / scored.length) * 100).toFixed(0)}%`}
          /* An `n` is not decoration. The source claims 80% and the only honest
             way to read ours is against how many trades produced it. */
          hint={`${wins} of ${scored.length} — source claims ~80%`}
        />
        <Card
          label="Avg return on debit"
          value={`${avgPct >= 0 ? "+" : ""}${avgPct.toFixed(0)}%`}
          hint="source claims +50–100% per winner"
        />
        <Card label="Net" value={formatPnL(net, 0)} />
        <Card
          label="Total risked"
          value={money(risked)}
          hint="sum of debits — every dollar was capped"
        />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-3xs">
          <thead className="text-text-dim">
            <tr className="text-left">
              <th className="py-1 pr-3 font-medium">date</th>
              <th className="py-1 pr-3 font-medium">exit</th>
              <th className="py-1 pr-3 font-medium text-right">debit</th>
              <th className="py-1 pr-3 font-medium text-right">P&amp;L</th>
              <th className="py-1 pr-3 font-medium text-right">% of debit</th>
              <th className="py-1 pr-3 font-medium text-right">held</th>
              <th className="py-1 font-medium">EM</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.date}-${r.entry_number}`} className="border-t border-border-dim">
                <td className="py-1 pr-3 text-text-secondary">{r.date}</td>
                <td className="py-1 pr-3 text-text-dim">{r.exit_reason}</td>
                <td className="py-1 pr-3 text-right text-text-dim">{money(r.total_debit)}</td>
                <td className="py-1 pr-3 text-right font-medium">
                  {formatPnL(r.realized_pnl ?? 0, 0)}
                </td>
                <td className="py-1 pr-3 text-right text-text-secondary">
                  {pct(r.pnl_pct_of_debit)}
                </td>
                <td className="py-1 pr-3 text-right text-text-dim">
                  {r.minutes_held?.toFixed(0) ?? "—"}m
                </td>
                <td className="py-1 text-text-dim">{r.em_source ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function LongStrangle() {
  const [status, setStatus] = useState<Status | null>(null);
  const [recent, setRecent] = useState<RecentRow[]>([]);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      fetch("/api/long-strangle/status")
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((d: Status) => {
          if (!cancelled) {
            setStatus(d);
            setFailed(false);
          }
        })
        .catch(() => {
          if (!cancelled) setFailed(true);
        });
    const loadRecent = () =>
      fetch("/api/long-strangle/recent?limit=30")
        .then((r) => (r.ok ? r.json() : { rows: [] }))
        .then((d: { rows: RecentRow[] }) => {
          if (!cancelled) setRecent(d.rows ?? []);
        })
        .catch(() => undefined);
    load();
    loadRecent();
    // 30s matches the other DB-backed pollers. H writes a snapshot roughly every
    // monitoring tick, so this is never the bottleneck on freshness.
    const t = setInterval(() => {
      load();
      loadRecent();
    }, 30_000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  if (failed) {
    return (
      <div className="p-4 text-sm text-text-secondary">
        Could not reach <code>/api/long-strangle/status</code>.
      </div>
    );
  }
  if (!status) return <div className="p-4 text-sm text-text-dim">Loading…</div>;

  const s = status.summary;

  return (
    <div className="px-3 pb-6 space-y-3">
      {/* The disclaimer LEADS, per the playbook: a P&L figure that looks real
          must not be mistaken for one. */}
      <div className="flex items-start gap-2 bg-bg-elevated border border-border-dim rounded p-3">
        <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" />
        <div className="text-xs text-text-secondary">
          {/* "LOCKED", not "shadow". Those are different states in this fleet:
              a shadow variant (A, C) merely has dry_run=true in config, while H
              — like D/E/F/G — REFUSES to construct outside dry-run. The stricter
              word is the true one, and the page should not undersell it. */}
          <span className="font-semibold text-text-primary">
            Strategy H — 0DTE Long Strangle · dry-run LOCKED.
          </span>{" "}
          Places <span className="font-semibold">no real orders</span>. The only
          long-gamma strategy in the fleet: it BUYS an OTM call and an OTM put at
          the expected move, so it profits on a large move in either direction and
          bleeds when the market sits still — the mirror of everything else here.
        </div>
      </div>

      {!status.available ? (
        <div className="bg-bg-elevated border border-border-dim rounded p-4 text-sm text-text-secondary">
          <div className="font-medium text-text-primary mb-1">No data yet.</div>
          {status.reason ?? "Variant H has not recorded a row."}
        </div>
      ) : (
        <>
          <div className="text-3xs uppercase tracking-wider text-text-dim">
            Session {status.date}
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <Card label="Open" value={String(s.open_count)} />
            <Card label="Closed" value={String(s.closed_count)} />
            <Card
              label="Debit deployed"
              value={money(s.debit_deployed)}
              /* The one claim no other strategy on this dashboard can make about
                 its own day. It is not a risk estimate — it is the worst case. */
              hint="= max possible loss, by construction"
            />
            <Card label="Realized" value={formatPnL(s.realized_pnl, 0)} />
          </div>

          {(() => {
            const p = status.open[0] ?? status.closed[0];
            const k = status.skipped[0];
            const call = p?.call_strike ?? k?.proposed_call_strike ?? null;
            const put = p?.put_strike ?? k?.proposed_put_strike ?? null;
            const spot = p?.spx_at_entry ?? k?.spx ?? null;
            const em = p?.expected_move ?? k?.expected_move ?? null;
            return (
              <ExpectedMoveBand
                path={status.spx_path ?? []}
                callStrike={call}
                putStrike={put}
                entrySpx={spot}
                em={em}
                hypothetical={!p && !!k}
                debit={p?.total_debit ?? k?.proposed_debit ?? null}
                sessionDate={status.date}
              />
            );
          })()}

          {status.open.length > 0 && (
            <section>
              <h2 className="text-xs font-semibold text-text-primary mb-1.5">Open</h2>
              <div className="space-y-2">
                {status.open.map((p) => (
                  <div
                    key={p.entry_number}
                    className="bg-bg-elevated border border-border-dim rounded p-3"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <div className="text-sm font-medium text-text-primary">
                        E#{p.entry_number} · C {p.call_strike} / P {p.put_strike} ·{" "}
                        {p.contracts}c
                      </div>
                      <div className="text-sm font-semibold">
                        {formatPnL(p.last_mark?.unrealized_pnl ?? 0, 0)}{" "}
                        <span className="text-text-secondary">
                          ({pct(p.last_mark?.pnl_pct_of_debit)})
                        </span>
                      </div>
                    </div>
                    <div className="text-3xs text-text-dim mt-1">
                      debit {money(p.total_debit)} · {p.em_source ?? "?"} EM{" "}
                      {p.expected_move?.toFixed(0) ?? "?"}pt · skew{" "}
                      {p.skew_gap_pct?.toFixed(1) ?? "?"}% · peak {pct(p.peak_pct)} ·
                      trough {pct(p.trough_pct)}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {status.closed.length > 0 && (
            <section>
              <h2 className="text-xs font-semibold text-text-primary mb-1.5">Closed</h2>
              <div className="space-y-2">
                {status.closed.map((p) => {
                  const gap = p.peak_minus_exit_pct;
                  return (
                    <div
                      key={p.entry_number}
                      className="bg-bg-elevated border border-border-dim rounded p-3"
                    >
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <div className="text-sm font-medium text-text-primary">
                          E#{p.entry_number} · C {p.call_strike} / P {p.put_strike} ·{" "}
                          {p.contracts}c
                          <span className="ml-2 text-3xs uppercase tracking-wider text-text-dim">
                            {p.exit_reason}
                          </span>
                        </div>
                        <div className="text-sm font-semibold">
                          {formatPnL(p.realized_pnl ?? 0, 0)}{" "}
                          <span className="text-text-secondary">
                            ({pct(p.pnl_pct_of_debit)})
                          </span>
                        </div>
                      </div>
                      <div className="text-3xs text-text-dim mt-1">
                        debit {money(p.total_debit)} · held{" "}
                        {p.minutes_held?.toFixed(0) ?? "?"} min · {p.em_source ?? "?"} EM{" "}
                        {p.expected_move?.toFixed(0) ?? "?"}pt
                      </div>
                      {/* THE measurement. Reached-but-not-captured is invisible in
                          realized P&L alone, and it is what the source's
                          80%-win-rate claim actually turns on. */}
                      {gap !== null && gap !== undefined && gap > 1 && (
                        <div className="flex items-center gap-1.5 mt-1.5 text-3xs text-warning">
                          <TrendingUp size={12} />
                          Peaked at {pct(p.peak_pct)} — gave back {gap.toFixed(0)}pp
                          before the exit landed
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {status.skipped.length > 0 && (
            <section>
              <h2 className="text-xs font-semibold text-text-primary mb-1.5">
                Declined
              </h2>
              <div className="text-3xs text-text-dim mb-1.5">
                Kept with the strikes and debit they would have used, so a veto can
                be scored later rather than reconstructed.
              </div>
              <div className="space-y-1.5">
                {status.skipped.map((k, i) => (
                  <div
                    key={`${k.entry_number}-${i}`}
                    className="bg-bg-elevated border border-border-dim rounded px-3 py-2"
                  >
                    <div className="text-xs text-text-primary">
                      E#{k.entry_number}{" "}
                      <span className="text-text-secondary">{k.skip_reason}</span>
                    </div>
                    <div className="text-3xs text-text-dim">
                      would have been C {k.proposed_call_strike} / P{" "}
                      {k.proposed_put_strike} for {money(k.proposed_debit)} ·{" "}
                      {k.em_source ?? "?"} EM {k.expected_move?.toFixed(0) ?? "?"}pt
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {status.open.length === 0 &&
            status.closed.length === 0 &&
            status.skipped.length === 0 && (
              <div className="text-sm text-text-dim">No activity on {status.date}.</div>
            )}
        </>
      )}

      {recent.length > 0 && <RunningRecord rows={recent} />}
    </div>
  );
}
