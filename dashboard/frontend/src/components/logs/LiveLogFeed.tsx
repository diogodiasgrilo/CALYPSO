import { useRef } from "react";
import { useHydraStore } from "../../store/hydraStore";
import { colors } from "../../lib/tradingColors";

const LEVEL_COLORS: Record<string, string> = {
  INFO: colors.textPrimary,
  WARNING: colors.warning,
  ERROR: colors.loss,
  CRITICAL: colors.loss,
  DEBUG: colors.textDim,
};

interface LiveLogFeedProps {
  /**
   * The strategy currently being VIEWED, when it is not the primary seat.
   *
   * The dashboard process tails ONE log — the primary's. This panel used to
   * render headed "Live Log" on every variant's page regardless, so selecting
   * G showed B's log lines under a heading that implied they were G's. The
   * module docstring in IronCondorDashboard asserted a note was shown
   * off-primary; there was no such note. Naming the source is the whole fix.
   */
  viewingStrategy?: string | null;
}

export function LiveLogFeed({ viewingStrategy }: LiveLogFeedProps = {}) {
  const { logLines } = useHydraStore();
  const scrollRef = useRef<HTMLDivElement>(null);

  if (logLines.length === 0) {
    return null;
  }

  // Show newest first (reversed), capped at 100
  const reversed = [...logLines].slice(-100).reverse();

  return (
    <div>
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
        Live Log
        {viewingStrategy && (
          <span className="ml-2 normal-case font-normal text-text-dim">
            — the live seat's process, not {viewingStrategy}'s
          </span>
        )}
      </h3>
      <div
        ref={scrollRef}
        className="bg-card rounded-lg border border-border-dim p-2 max-h-[10rem] overflow-y-auto font-mono text-2xs leading-relaxed"
      >
        {reversed.map((line, i) => (
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
