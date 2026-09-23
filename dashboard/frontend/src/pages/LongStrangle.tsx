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
 * Everything here is dry-run. H places no real orders and is not installed on the
 * VM, so `available: false` is the expected response today and the page says so
 * plainly rather than rendering an empty grid.
 */

import { useEffect, useState } from "react";
import { AlertTriangle, TrendingUp } from "lucide-react";
import { formatCurrency, formatPnL } from "../lib/formatters";

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
  proposed_call_strike: number | null;
  proposed_put_strike: number | null;
  proposed_debit: number | null;
  em_source: string | null;
  expected_move: number | null;
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

function Card({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="bg-bg-elevated rounded p-3 border border-border-dim">
      <div className="text-3xs uppercase tracking-wider text-text-dim">{label}</div>
      <div className="text-lg font-semibold text-text-primary mt-0.5">{value}</div>
      {hint && <div className="text-3xs text-text-dim mt-0.5">{hint}</div>}
    </div>
  );
}

export function LongStrangle() {
  const [status, setStatus] = useState<Status | null>(null);
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
    load();
    // 30s matches the other DB-backed pollers. H writes a snapshot roughly every
    // monitoring tick, so this is never the bottleneck on freshness.
    const t = setInterval(load, 30_000);
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
          <span className="font-semibold text-text-primary">
            Strategy H — 0DTE Long Strangle · dry-run shadow.
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
    </div>
  );
}
