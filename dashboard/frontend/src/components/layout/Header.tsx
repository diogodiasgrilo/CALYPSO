import { Volume2, VolumeX } from "lucide-react";
import { useHydraStore } from "../../store/hydraStore";
import { formatPrice } from "../../lib/formatters";
import { vixColor, colors } from "../../lib/tradingColors";
import { useFlashOnChange } from "../../hooks/useFlashOnChange";
import { isMuted, toggleMute } from "../../lib/sounds";
import { useState } from "react";

export function Header() {
  const { connectionStatus, hydraState, market, todayOHLC } = useHydraStore();
  const [muted, setMuted] = useState(isMuted());

  // SPX: use last OHLC bar close (most recent), fall back to state file high/low midpoint
  const lastBar = todayOHLC.length > 0 ? todayOHLC[todayOHLC.length - 1] : null;
  const ohlc = hydraState?.market_data_ohlc;
  const spx = lastBar?.close ?? (ohlc?.spx_high ? (ohlc.spx_high + ohlc.spx_low) / 2 : 0);
  const vix = lastBar?.vix ?? (ohlc?.vix_high ? (ohlc.vix_high + ohlc.vix_low) / 2 : 0);

  const spxFlash = useFlashOnChange(spx);
  const vixFlash = useFlashOnChange(vix);

  const connDot =
    connectionStatus === "connected"
      ? "bg-profit pulse-live"
      : connectionStatus === "connecting"
      ? "bg-warning"
      : "bg-loss";

  const handleMuteToggle = () => {
    toggleMute();
    setMuted(isMuted());
  };

  return (
    <header className="flex items-center justify-between px-4 max-sm:px-2 py-2 bg-bg border-b border-border-dim">
      {/* Left: Logo + title + connection */}
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
        <div className="flex items-center gap-1.5 ml-2 max-sm:ml-1">
          <div className={`w-2 h-2 rounded-full ${connDot}`} />
          <span className="text-text-secondary text-xs capitalize hidden sm:inline">
            {connectionStatus}
          </span>
        </div>
      </div>

      {/* Center: SPX + VIX */}
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
            <span className="text-text-secondary mr-1">SPX</span>
            <span className="text-text-primary font-semibold">
              {formatPrice(spx)}
            </span>
          </div>
        )}
        {vix > 0 && (
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
      </div>
    </header>
  );
}
