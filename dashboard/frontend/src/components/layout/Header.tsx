import { Volume2, VolumeX, LogOut } from "lucide-react";
import { useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import * as auth from "../../auth";
import { useHydraStore } from "../../store/hydraStore";
import { formatPrice } from "../../lib/formatters";
import { vixColor, colors } from "../../lib/tradingColors";
import { useFlashOnChange } from "../../hooks/useFlashOnChange";
import { isMuted, toggleMute } from "../../lib/sounds";
import { useBotConfig } from "../../hooks/useBotConfig";
import { useStrategyMeta } from "../../hooks/useStrategyMeta";
import { useSelectedStrategy } from "../../hooks/useSelectedStrategy";
import { useSelectedSnapshotStore } from "../dashboard/selectedSnapshotStore";
import { StrategyPicker } from "../shared/StrategyPicker";
import type { ICSnapshotBody } from "../../hooks/useStrategySnapshot";

export function Header() {
  const { connectionStatus, hydraState, market, todayOHLC } = useHydraStore();
  const [muted, setMuted] = useState(isMuted());
  const cfg = useBotConfig();
  const meta = useStrategyMeta();
  const { strategy, isPrimarySelected } = useSelectedStrategy();
  const selectedSnapshot = useSelectedSnapshotStore((s) => s.snapshot);
  const location = useLocation();
  const [username, setUsername] = useState<string | null>(null);

  useEffect(() => {
    auth.getCurrentUser().then(setUsername);
  }, []);

  // The picker drives the Dashboard live view AND (since 2026-06-22) the
  // per-strategy History/Analytics tabs. It's hidden on tabs it does NOT
  // control (e.g. /comparison, /dc), which instead show a note so the picker
  // never silently lies about what those pages display.
  const onPickerTab = ["/", "/history", "/analytics"].includes(location.pathname);

  // ── Header chrome source resolution (audit AUD-3-F1) ──
  // When a NON-primary strategy is selected AND its snapshot has arrived, the
  // WHOLE header re-binds to that selection: label, dry-run banner, and the
  // underlying prices come from the snapshot envelope/body — NOT the WS store
  // (which always tracks the primary C). On the primary (or before the snapshot
  // loads), we use the original WS-driven chrome exactly as before.
  const usingSelected = !!selectedSnapshot && !isPrimarySelected;

  // Dry-run flag follows the selection.
  const dryRun = usingSelected
    ? selectedSnapshot!.dry_run === true
    : cfg.dry_run === true;

  // Label follows the selection (display_name/label from the envelope).
  const primaryLabel = usingSelected
    ? selectedSnapshot!.label || selectedSnapshot!.display_name
    : cfg.primary_label;

  // Underlying symbol (defaults SPX for the primary IC stack).
  const underlying = usingSelected
    ? selectedSnapshot!.underlying_symbol || "SPX"
    : "SPX";

  // Price source. WS primary: last OHLC bar / state-file midpoint. Selected:
  // the snapshot body's spx fields (IC) — calendars carry no intraday SPX/VIX,
  // so we suppress the price chips for them rather than show stale primary data.
  let spx = 0;
  let vix = 0;
  let showVix = true;
  if (usingSelected) {
    if (selectedSnapshot!.data_kind === "ic_state") {
      const body = selectedSnapshot!.body as ICSnapshotBody;
      const high = body.spx_high ?? 0;
      const low = body.spx_low ?? 0;
      spx = body.spx_price ?? (high && low ? (high + low) / 2 : 0);
      // The IC snapshot body has vix_open but no live VIX; show open as a hint.
      vix = body.vix_open ?? 0;
    } else {
      // Calendar strategy — no intraday SPX/VIX in the body.
      spx = 0;
      vix = 0;
      showVix = false;
    }
  } else {
    const lastBar = todayOHLC.length > 0 ? todayOHLC[todayOHLC.length - 1] : null;
    const ohlc = hydraState?.market_data_ohlc;
    spx = lastBar?.close ?? (ohlc?.spx_high ? (ohlc.spx_high + ohlc.spx_low) / 2 : 0);
    vix = lastBar?.vix ?? (ohlc?.vix_high ? (ohlc.vix_high + ohlc.vix_low) / 2 : 0);
  }

  const spxFlash = useFlashOnChange(spx);
  const vixFlash = useFlashOnChange(vix);

  const connDot =
    connectionStatus === "connected"
      ? "bg-profit pulse-live"
      : connectionStatus === "connecting"
      ? "bg-warning"
      : "bg-loss";
  const connLabel = connectionStatus === "auth_expired" ? "signed out" : connectionStatus;

  const handleMuteToggle = () => {
    toggleMute();
    setMuted(isMuted());
  };

  const showPicker = onPickerTab && !meta.loading;
  // Note only on tabs the picker does NOT control (e.g. /comparison, /dc), so
  // they never imply the picker's selection applies to them.
  const primaryName = meta.byId[meta.primaryId]?.display_name;
  const showOffTabNote = !onPickerTab && !meta.loading && !!strategy && !isPrimarySelected && !!primaryName;

  return (
    <>
      {/* Dry-run banner — follows the SELECTED strategy. */}
      {dryRun && (
        <div
          className="w-full text-center font-bold tracking-widest text-xs sm:text-sm py-1.5 max-sm:py-1 select-none"
          style={{
            background: "repeating-linear-gradient(45deg, rgba(210,153,34,0.95) 0 14px, rgba(180,128,18,0.95) 14px 28px)",
            color: "#1a1a1a",
            letterSpacing: "0.15em",
          }}
          title="Dry-run mode: real broker prices, no real orders placed."
        >
          ⚠ DRY-RUN MODE — REAL PRICES, NO REAL ORDERS — POSITION IDs PREFIXED DRY_* ⚠
        </div>
      )}
      <header className="flex items-center justify-between px-4 max-sm:px-2 py-2 bg-bg border-b border-border-dim">
      {/* Left: Logo + title + connection + strategy picker */}
      <div className="flex items-center gap-3 max-sm:gap-2">
        <img
          src="/hydra-logo.png"
          alt="HYDRA"
          className="h-8 w-8 max-sm:h-6 max-sm:w-6 rounded"
          onError={(e) => {
            (e.target as HTMLImageElement).style.display = "none";
          }}
        />
        <span className="text-text-primary font-bold text-base max-sm:text-sm tracking-wide">
          HYDRA
        </span>

        {/* Strategy picker (dashboard tab only). */}
        {showPicker && <StrategyPicker />}

        {/* Static label fallback when the picker isn't shown (other tabs) or
            there's only one strategy. The label follows the selection. */}
        {!showPicker && primaryLabel && (
          <span
            className="text-[10px] sm:text-xs font-bold px-2 py-0.5 rounded uppercase tracking-wide whitespace-nowrap"
            style={{
              backgroundColor: dryRun ? "rgba(210,153,34,0.18)" : "rgba(126,232,199,0.18)",
              color: dryRun ? colors.warning : colors.profit,
            }}
            title="The strategy this page is showing."
          >
            {primaryLabel}
          </span>
        )}

        {/* Off-dashboard note — History/Analytics are primary-bound. */}
        {showOffTabNote && (
          <span
            className="text-[10px] sm:text-xs px-2 py-0.5 rounded whitespace-nowrap text-text-secondary"
            style={{ backgroundColor: "var(--bg-elevated)" }}
            title="History and Analytics show the primary strategy regardless of the dashboard picker selection."
          >
            shows {primaryName}
          </span>
        )}

        <div className="flex items-center gap-1.5 ml-2 max-sm:ml-1">
          <div className={`w-2 h-2 rounded-full ${connDot}`} />
          <span className="text-text-secondary text-xs capitalize hidden sm:inline">
            {connLabel}
          </span>
        </div>
      </div>

      {/* Center: underlying + VIX */}
      <div className="flex items-center gap-6 max-sm:gap-3">
        {spx > 0 && (
          <div
            className={`text-sm max-sm:text-xs ${
              spxFlash === "up"
                ? "flash-up"
                : spxFlash === "down"
                ? "flash-down"
                : ""
            }`}
          >
            <span className="text-text-secondary mr-1">{underlying}</span>
            <span className="text-text-primary font-semibold">
              {formatPrice(spx)}
            </span>
          </div>
        )}
        {showVix && vix > 0 && (
          <div
            className={`text-sm max-sm:text-xs ${
              vixFlash === "up"
                ? "flash-up"
                : vixFlash === "down"
                ? "flash-down"
                : ""
            }`}
          >
            <span className="text-text-secondary mr-1">VIX</span>
            <span className="font-semibold" style={{ color: vixColor(vix) }}>
              {vix.toFixed(1)}
            </span>
          </div>
        )}
      </div>

      {/* Right: Market status + mute */}
      <div className="flex items-center gap-4 max-sm:gap-2">
        {market && (
          <div className="flex items-center gap-1.5">
            {market.is_fomc_day && (
              <span
                className="text-xs px-2 py-0.5 rounded font-semibold max-sm:hidden"
                style={{ backgroundColor: "rgba(210, 153, 34, 0.2)", color: colors.warning }}
              >
                FOMC
              </span>
            )}
            <span
              className="text-xs px-2 max-sm:px-1 py-0.5 rounded"
              style={
                market.is_open
                  ? { backgroundColor: "rgba(126, 232, 199, 0.2)", color: colors.profit }
                  : !market.is_trading_day && market.holiday_name
                    ? { backgroundColor: "rgba(248, 81, 73, 0.2)", color: colors.loss }
                    : market.session === "pre_market"
                      ? { backgroundColor: "rgba(88, 166, 255, 0.2)", color: colors.info }
                      : market.session === "after_hours"
                        ? { backgroundColor: "rgba(88, 166, 255, 0.2)", color: colors.info }
                        : { backgroundColor: "var(--bg-elevated)", color: "var(--text-secondary)" }
              }
            >
              {market.is_open
                ? "OPEN"
                : !market.is_trading_day && market.holiday_name
                  ? "HOLIDAY"
                  : !market.is_trading_day
                    ? "WEEKEND"
                    : market.session === "pre_market"
                      ? "PRE-MKT"
                      : market.session === "after_hours"
                        ? "AH"
                        : "CLOSED"}
            </span>
          </div>
        )}
        <button
          onClick={handleMuteToggle}
          className="text-text-secondary hover:text-text-primary transition-colors"
          title={muted ? "Unmute" : "Mute"}
        >
          {muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
        </button>
        {username && (
          <div className="flex items-center gap-2">
            <span className="text-text-secondary text-xs hidden sm:inline" title="Signed in">
              {username}
            </span>
            <button
              onClick={async () => {
                await auth.logout();
                window.location.reload();
              }}
              className="text-text-secondary hover:text-loss transition-colors"
              title="Sign out"
            >
              <LogOut size={16} />
            </button>
          </div>
        )}
      </div>
    </header>
    </>
  );
}
