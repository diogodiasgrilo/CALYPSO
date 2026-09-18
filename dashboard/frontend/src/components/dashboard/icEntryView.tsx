/**
 * Shared iron-condor per-entry / per-side rendering.
 *
 * Extracted so BOTH the Comparison panels and the polled (non-primary) main
 * dashboard render entries identically — same per-side lifecycle resolution,
 * same cushion bars, same disposition badges. The logic is lifted verbatim from
 * the original Comparison.tsx so behavior is unchanged.
 *
 * All inputs are plain snapshot/comparison fields (no store coupling), so this
 * works for the WS body, the /api/variants payload, and the
 * /api/strategies/{id}/snapshot IC body alike.
 */

import { colors, pnlColor, cushionColor } from "../../lib/tradingColors";
import { resolveSide, type ICEntry, type SideInfo } from "./icEntryModel";
import { formatPnL } from "../../lib/formatters";

// ── Per-side lifecycle resolution ───────────────────────────────────────────

function mutedCushion(pct: number): string {
  if (pct >= 40) return colors.profitMuted;
  if (pct >= 25) return colors.warningMuted;
  if (pct >= 15) return "#a6602b";
  return colors.lossMuted;
}

export function BufferBar({
  pct,
  label,
  compact = false,
  muted = false,
}: {
  pct: number | null;
  label?: string;
  compact?: boolean;
  muted?: boolean;
}) {
  const v = pct ?? 100;
  const color = muted ? mutedCushion(v) : cushionColor(v);
  const height = compact ? "h-1.5" : "h-2";

  return (
    <div className={compact ? "" : "flex-1"}>
      {label && (
        <div className="flex justify-between text-3xs mb-0.5">
          <span className="text-text-secondary">{label}</span>
          <span className="font-mono" style={{ color }}>
            {pct === null ? "—" : `${v.toFixed(0)}%`}
          </span>
        </div>
      )}
      <div className={`${height} bg-bg-elevated rounded-full overflow-hidden`}>
        <div
          className={`${height} rounded-full transition-all duration-500`}
          style={{ width: `${Math.max(0, Math.min(v, 100))}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}

/** One side (call or put) of an entry — strikes + a state-specific second line. */
export function SideLine({ tag, info }: { tag: "C" | "P"; info: SideInfo }) {
  if (info.state === "absent") {
    return (
      <div className="text-text-dim">
        <span className="text-text-dim">{tag}:</span> —
      </div>
    );
  }
  const live = info.state === "live";
  const strikeColor = live ? colors.textPrimary : colors.textSecondary;
  const realizedStr =
    info.realized !== null ? `${info.realized > 0 ? "+" : ""}$${info.realized.toFixed(0)}` : "";

  return (
    <div>
      <div>
        <span className="text-text-dim">{tag}:</span>{" "}
        <span style={{ color: strikeColor }}>
          {info.shortStrike}/{info.longStrike}
        </span>
        <span className="text-text-dim ml-1.5">cr ${info.credit?.toFixed(0)}</span>
      </div>

      {live && info.cushionPct !== null && (
        <div
          className="mt-0.5"
          title={`close $${info.cost?.toFixed(0)} → stop $${info.stop?.toFixed(0)} (effective)`}
        >
          <div className="flex justify-between text-3xs leading-none mb-0.5">
            <span className="text-text-dim">
              {info.distancePt != null
                ? info.distancePt >= 0
                  ? `${info.distancePt.toFixed(0)}pt OTM`
                  : `ITM ${Math.abs(info.distancePt).toFixed(0)}pt`
                : `close $${info.cost?.toFixed(0)} → $${info.stop?.toFixed(0)}`}
            </span>
            <span className="font-mono" style={{ color: cushionColor(info.cushionPct) }}>
              {info.cushionPct < 15 ? "⚠ " : ""}
              {info.cushionPct.toFixed(0)}%
            </span>
          </div>
          <BufferBar pct={info.cushionPct} compact />
        </div>
      )}

      {(info.state === "stopped" || info.state === "breach") && (
        <div
          className="mt-0.5 font-mono"
          style={{ color: info.state === "breach" ? colors.warning : colors.loss }}
        >
          {info.state === "breach" ? "⚠ BREACH" : "✗ STOPPED"}
          {realizedStr && ` ${realizedStr}`}
          {info.closeTime && <span className="text-text-dim"> @{info.closeTime}</span>}
        </div>
      )}

      {info.state === "expired" && (
        <div className="mt-0.5 font-mono" style={{ color: colors.profit }}>
          ✓ kept {realizedStr || "credit"}
        </div>
      )}

      {info.state === "skipped" && <div className="mt-0.5 text-text-dim">– skipped</div>}
    </div>
  );
}

export function ICEntryRow({ entry, accent, spx }: { entry: ICEntry; accent: string; spx?: number }) {
  const num = entry.entry_number ?? "?";
  const time = entry.entry_time?.slice(11, 16) ?? "—";
  const disposition = entry.disposition ?? "LIVE";

  const fullySkipped =
    disposition === "SKIPPED" ||
    (entry.call_side_skipped && entry.put_side_skipped) ||
    (!entry.short_call_strike && !entry.short_put_strike);
  if (fullySkipped) {
    return (
      <div className="rounded border border-border-dim bg-bg p-2 text-xs flex items-center justify-between">
        <span className="font-mono">
          <span style={{ color: accent }}>#{num}</span>{" "}
          <span className="text-text-secondary">{time}</span>
        </span>
        <span
          className="text-3xs font-mono uppercase tracking-wider"
          style={{ color: colors.textDim }}
        >
          skipped{entry.skip_reason ? ` · ${entry.skip_reason}` : " · credit gate"}
        </span>
      </div>
    );
  }

  const call = resolveSide(entry, "call", spx);
  const put = resolveSide(entry, "put", spx);
  const anyLive = call.state === "live" || put.state === "live";
  const anyStopped =
    call.state === "stopped" || call.state === "breach" ||
    put.state === "stopped" || put.state === "breach";

  let badge: string;
  let badgeColor: string;
  if (anyLive && anyStopped) {
    badge = "PARTIAL";
    badgeColor = colors.warning;
  } else if (anyLive) {
    badge = disposition === "SETTLING" ? "SETTLING" : "LIVE";
    badgeColor = disposition === "SETTLING" ? colors.textSecondary : accent;
  } else {
    badge = disposition;
    badgeColor =
      disposition === "TP" || disposition === "EXPIRED" ? colors.profit :
      disposition === "STOP" || disposition === "BREACH" ? colors.loss :
      disposition === "SKIPPED" ? colors.warning :
      colors.textSecondary;
  }

  const realizedTotal = (call.realized ?? 0) + (put.realized ?? 0);
  const liveUnrealized = (call.unrealized ?? 0) + (put.unrealized ?? 0);
  const currentPnl = realizedTotal + liveUnrealized;

  return (
    <div className="rounded border border-border-dim bg-bg p-2 text-xs">
      <div className="flex items-center justify-between mb-1">
        <div className="font-mono">
          <span style={{ color: accent }}>#{num}</span>{" "}
          <span className="text-text-secondary">{time}</span>
        </div>
        <div className="flex items-center gap-2">
          {Math.round(currentPnl) !== 0 && (
            <span className="text-2xs font-mono" style={{ color: pnlColor(currentPnl) }}>
              {currentPnl > 0 ? "+" : ""}${currentPnl.toFixed(0)}
            </span>
          )}
          <span
            className="text-3xs font-mono uppercase tracking-wider"
            style={{ color: badgeColor }}
          >
            {badge}
          </span>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 font-mono text-text-secondary">
        <SideLine tag="C" info={call} />
        <SideLine tag="P" info={put} />
      </div>

      {/* Brandon defensive overlay(s) — same block as EntryCard.tsx's primary-view
          rendering, ported here so the Comparison page shows the identical hedge
          detail instead of silently dropping it (2026-08-15). */}
      {Array.isArray(entry.overlays) && entry.overlays.length > 0 && (
        <div className="mt-2 space-y-1">
          {entry.overlays.map((ov, i) => {
            const strikes = ov.legs.map((l) => l.strike).join("/");
            const name = `${ov.threatened_side} ${String(ov.structure).replace(/_/g, " ")}`;
            return (
              <div
                key={i}
                className="rounded px-2 py-1 text-3xs"
                style={{ backgroundColor: colors.bgElevated, borderLeft: `2px solid ${colors.warning}` }}
              >
                <div className="flex items-center justify-between">
                  <span className="uppercase tracking-wide" style={{ color: colors.warning }}>
                    ⚡ Overlay · {name}
                  </span>
                  {ov.pnl != null && (
                    <span style={{ color: pnlColor(ov.pnl) }}>{formatPnL(ov.pnl, 0)}</span>
                  )}
                </div>
                <div className="flex items-center justify-between mt-0.5 text-text-secondary">
                  <span>{strikes}</span>
                  <span>debit ${Math.abs(ov.debit).toFixed(0)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
