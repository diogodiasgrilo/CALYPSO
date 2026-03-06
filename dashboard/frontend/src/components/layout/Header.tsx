import { Volume2, VolumeX } from "lucide-react";
import { useHydraStore } from "../../store/hydraStore";
import { formatPrice } from "../../lib/formatters";
import { vixColor } from "../../lib/tradingColors";
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
    <header className="flex items-center justify-between px-4 py-2 bg-bg border-b border-border-dim">
      {/* Left: Logo + title + connection */}
      <div className="flex items-center gap-3">
        <img
          src="/hydra-logo.png"
          alt="HYDRA"
          className="h-7 w-7"
          onError={(e) => {
            (e.target as HTMLImageElement).style.display = "none";
          }}
        />
        <span className="text-text-primary font-bold text-base tracking-wide">
          CALYPSO
        </span>
        <div className="flex items-center gap-1.5 ml-2">
          <div className={`w-2 h-2 rounded-full ${connDot}`} />
          <span className="text-text-secondary text-xs capitalize">
            {connectionStatus}
          </span>
        </div>
      </div>

      {/* Center: SPX + VIX */}
      <div className="flex items-center gap-6">
        {spx > 0 && (
          <div
            className={`text-sm ${
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
            className={`text-sm ${
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

      {/* Right: Market status + mute + clock */}
      <div className="flex items-center gap-4">
        {market && (
          <span
            className={`text-xs px-2 py-0.5 rounded ${
              market.is_open
                ? "bg-profit/20 text-profit"
                : "bg-bg-elevated text-text-secondary"
            }`}
          >
            {market.is_open ? "MARKET OPEN" : "CLOSED"}
          </span>
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
