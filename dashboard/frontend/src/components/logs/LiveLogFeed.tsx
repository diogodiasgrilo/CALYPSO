import { useRef, useEffect, useState } from "react";
import { useHydraStore } from "../../store/hydraStore";
import { colors } from "../../lib/tradingColors";

const LEVEL_COLORS: Record<string, string> = {
  INFO: colors.textPrimary,
  WARNING: colors.warning,
  ERROR: colors.loss,
  CRITICAL: colors.loss,
  DEBUG: colors.textDim,
};

export function LiveLogFeed() {
  const { logLines } = useHydraStore();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logLines, autoScroll]);

  const handleScroll = () => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 30);
  };

  if (logLines.length === 0) {
    return null;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">
          Live Log
        </h3>
        <button
          onClick={() => setAutoScroll(!autoScroll)}
          className={`text-[10px] px-1.5 py-0.5 rounded ${
            autoScroll
              ? "bg-profit/20 text-profit"
              : "bg-bg-elevated text-text-secondary"
          }`}
        >
          {autoScroll ? "AUTO" : "PAUSED"}
        </button>
      </div>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="bg-card rounded-lg border border-border-dim p-2 h-40 overflow-y-auto font-mono text-[11px] leading-relaxed"
      >
        {logLines.slice(-100).map((line, i) => (
          <div key={i} className="flex gap-2 hover:bg-bg-elevated/50 px-1">
            {line.timestamp && (
              <span className="text-text-dim shrink-0">
                {line.timestamp.slice(11, 19)}
              </span>
            )}
            {line.level && (
              <span
                className="shrink-0 w-6"
                style={{ color: LEVEL_COLORS[line.level] ?? colors.textDim }}
              >
                {line.level.slice(0, 1)}
              </span>
            )}
            <span className="text-text-primary break-all">{line.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
