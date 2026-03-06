import { cushionColor, colors } from "../../lib/tradingColors";

interface CushionBarProps {
  label: string;
  percentage: number;
  skipped?: boolean;
  stopped?: boolean;
  active?: boolean;
}

export function CushionBar({ label, percentage, skipped, stopped, active }: CushionBarProps) {
  if (skipped) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <span className="text-text-dim w-6">{label}</span>
        <span className="text-text-dim">SKIPPED</span>
      </div>
    );
  }

  if (stopped) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <span className="text-text-dim w-6">{label}</span>
        <span style={{ color: colors.loss }} className="font-semibold">STOPPED</span>
      </div>
    );
  }

  if (!active) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <span className="text-text-dim w-6">{label}</span>
        <span className="text-text-dim">--</span>
      </div>
    );
  }

  const pct = Math.max(0, Math.min(100, percentage));
  const color = cushionColor(pct);

  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-text-secondary w-6">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-bg-elevated overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500 ease-out"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="w-10 text-right font-mono" style={{ color }}>
        {pct.toFixed(0)}%
      </span>
    </div>
  );
}
