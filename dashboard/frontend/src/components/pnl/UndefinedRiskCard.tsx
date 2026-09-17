/**
 * Risk card for a strategy whose loss has NO structural cap (dashboard rebuild
 * Phase 10, defect D2's sibling).
 *
 * `CAPITAL_BASES.broker_margin.boundedLoss` has been `false` since Phase 4 with
 * the contract "printing any max-loss figure is a lie, so the card shows
 * UNBOUNDED instead of a number" — and nothing consumed it. Nothing on the
 * dashboard rendered a false max loss either, so this was not an incorrect
 * number; it was an ABSENT one. A naked short strangle sat beside four
 * defined-risk strategies with no visible signal that its downside behaves
 * completely differently.
 *
 * WHY DISTANCE-TO-SHORT IS THE RIGHT SECOND NUMBER. "Max loss" cannot be
 * quantified here, but "how far is the market from the strike that starts
 * losing money" can be, and it is the figure that actually characterises the
 * risk. Expressed BOTH in points and as a fraction of the day's expected move,
 * because 30 points means nothing without knowing whether the market typically
 * travels 20 or 120 in a session.
 *
 * Expected move uses the standard one-sigma daily convention
 * `spot x (VIX/100) / sqrt(252)`. It is labelled as "today's expected move"
 * rather than "sigma" so no reader mistakes it for a realised statistic.
 *
 * The maths lives in `lib/undefinedRisk.ts` so it can be unit-tested against
 * known answers independently of rendering.
 */

import { AlertTriangle } from "lucide-react";
import { colors } from "../../lib/tradingColors";
import type { HydraEntry } from "../../store/hydraStore";
import {
  expectedDailyMove,
  nearestShortDistance,
} from "../../lib/undefinedRisk";

export function UndefinedRiskCard({
  entries,
  spx,
  vix,
  displayName,
}: {
  entries: HydraEntry[];
  spx?: number | null;
  vix?: number | null;
  displayName?: string;
}) {
  const em = expectedDailyMove(spx, vix);
  const nearest = nearestShortDistance(entries, spx);
  const inEm = nearest && em ? nearest.points / em : null;

  // Colour the distance by how close the market is, in EXPECTED-MOVE terms —
  // absolute points are meaningless without the day's volatility.
  const distanceColor =
    inEm === null
      ? colors.textDim
      : inEm <= 0
        ? colors.loss
        : inEm < 0.5
          ? colors.loss
          : inEm < 1
            ? colors.warning
            : colors.profit;

  return (
    <div
      className="rounded-lg border p-4"
      style={{
        backgroundColor: "rgba(210, 153, 34, 0.06)",
        borderColor: "rgba(210, 153, 34, 0.28)",
      }}
    >
      <div className="flex items-center gap-2 mb-3">
        <AlertTriangle size={14} style={{ color: colors.warning }} />
        <span
          className="label-upper"
          style={{ color: colors.warning, letterSpacing: "0.1em" }}
        >
          Undefined risk
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4 max-sm:grid-cols-1">
        <div>
          <div className="text-3xs uppercase tracking-wider text-text-dim mb-1">
            Max loss
          </div>
          <div
            className="text-xl font-semibold tracking-tight"
            style={{ color: colors.warning }}
          >
            UNBOUNDED
          </div>
        </div>

        <div>
          <div className="text-3xs uppercase tracking-wider text-text-dim mb-1">
            Nearest short
          </div>
          {nearest ? (
            <div className="text-xl font-semibold tracking-tight font-mono"
                 style={{ color: distanceColor }}>
              {nearest.points >= 0 ? "" : "−"}
              {Math.abs(nearest.points).toFixed(0)} pt
              {inEm !== null && (
                <span className="text-xs font-normal ml-1.5 text-text-secondary">
                  ({inEm.toFixed(2)}× EM)
                </span>
              )}
            </div>
          ) : (
            <div className="text-xl font-semibold tracking-tight text-text-dim">—</div>
          )}
          <div className="text-3xs text-text-dim mt-0.5">
            {nearest
              ? `short ${nearest.side} ${nearest.strike.toFixed(0)}${
                  nearest.points < 0 ? " — BREACHED" : ""
                }`
              : "no open position"}
          </div>
        </div>
      </div>

      <p className="text-2xs text-text-secondary leading-relaxed mt-3">
        {displayName ?? "This strategy"} sells naked options — there is no long
        wing capping the loss, so a large move can cost more than the margin
        held. Capital shown elsewhere is the broker requirement, not a maximum
        loss.
        {em !== null && (
          <> Today&apos;s expected move is ±{em.toFixed(0)} pts.</>
        )}
      </p>
    </div>
  );
}
