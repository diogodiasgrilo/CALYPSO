import { useState, useEffect, useMemo } from "react";
import { FlaskConical, Database, TrendingUp, Shield, Gauge } from "lucide-react";
import { colors } from "../../lib/tradingColors";

interface SimCountdownProps {
  requiredDays: number;
  collectedDays: number;
  dataStartDate: string;
  allDates: string[];
}

/** Estimate the unlock date given trading days remaining. */
function estimateUnlockDate(daysRemaining: number): Date {
  const now = new Date();
  let count = 0;
  const d = new Date(now);
  while (count < daysRemaining) {
    d.setDate(d.getDate() + 1);
    const dow = d.getDay();
    if (dow !== 0 && dow !== 6) count++; // skip weekends
  }
  return d;
}

function useCountdownTimer(targetDate: Date) {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const diff = targetDate.getTime() - now.getTime();
  if (diff <= 0) return { days: 0, hours: 0, minutes: 0, seconds: 0 };

  return {
    days: Math.floor(diff / 86400000),
    hours: Math.floor((diff % 86400000) / 3600000),
    minutes: Math.floor((diff % 3600000) / 60000),
    seconds: Math.floor((diff % 60000) / 1000),
  };
}

function CountdownDigit({ value, label }: { value: number; label: string }) {
  return (
    <div className="flex flex-col items-center">
      <div
        className="text-4xl sm:text-5xl font-bold tabular-nums tracking-tight"
        style={{ color: colors.profit }}
      >
        {String(value).padStart(2, "0")}
      </div>
      <div className="text-[10px] uppercase tracking-widest mt-1" style={{ color: colors.textDim }}>
        {label}
      </div>
    </div>
  );
}

function FeatureCard({ icon: Icon, title, desc }: { icon: typeof TrendingUp; title: string; desc: string }) {
  return (
    <div className="bg-card rounded-lg border border-border-dim p-4 flex gap-3 items-start">
      <div className="mt-0.5 shrink-0" style={{ color: colors.info }}>
        <Icon size={18} />
      </div>
      <div>
        <div className="text-sm font-medium text-text-primary">{title}</div>
        <div className="text-xs text-text-secondary mt-0.5">{desc}</div>
      </div>
    </div>
  );
}

export function SimCountdown({ requiredDays, collectedDays, dataStartDate, allDates }: SimCountdownProps) {
  const daysRemaining = Math.max(0, requiredDays - collectedDays);
  const progress = Math.min(100, (collectedDays / requiredDays) * 100);
  const unlockDate = useMemo(() => estimateUnlockDate(daysRemaining), [daysRemaining]);
  const countdown = useCountdownTimer(unlockDate);

  return (
    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-120px)] px-4">
      {/* Icon */}
      <div
        className="w-16 h-16 rounded-2xl flex items-center justify-center mb-6"
        style={{ backgroundColor: `${colors.info}15`, border: `1px solid ${colors.info}30` }}
      >
        <FlaskConical size={32} style={{ color: colors.info }} />
      </div>

      {/* Title */}
      <h1 className="text-2xl sm:text-3xl font-bold text-text-primary mb-2">
        Strategy Simulator
      </h1>
      <p className="text-sm text-text-secondary text-center max-w-md mb-8">
        Replay historical trading days with different parameters. See the impact before you deploy.
      </p>

      {/* Progress ring */}
      <div className="relative w-48 h-48 mb-6">
        <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
          {/* Background ring */}
          <circle
            cx="50" cy="50" r="42"
            fill="none"
            stroke={colors.borderDim}
            strokeWidth="6"
          />
          {/* Progress ring */}
          <circle
            cx="50" cy="50" r="42"
            fill="none"
            stroke={colors.info}
            strokeWidth="6"
            strokeLinecap="round"
            strokeDasharray={`${progress * 2.639} ${264 - progress * 2.639}`}
            className="transition-all duration-1000"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-3xl font-bold text-text-primary">{collectedDays}</span>
          <span className="text-xs text-text-secondary">of {requiredDays} days</span>
        </div>
      </div>

      {/* Countdown clock */}
      {daysRemaining > 0 && (
        <div className="mb-8">
          <div className="text-xs text-text-dim uppercase tracking-widest text-center mb-3">
            Estimated time to unlock
          </div>
          <div className="flex gap-6 sm:gap-8">
            <CountdownDigit value={countdown.days} label="Days" />
            <CountdownDigit value={countdown.hours} label="Hours" />
            <CountdownDigit value={countdown.minutes} label="Min" />
            <CountdownDigit value={countdown.seconds} label="Sec" />
          </div>
          <div className="text-xs text-text-dim text-center mt-3">
            ~{unlockDate.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })}
          </div>
        </div>
      )}

      {/* Progress bar */}
      <div className="w-full max-w-sm mb-8">
        <div className="flex justify-between text-[10px] text-text-dim mb-1">
          <span>Collecting data since {new Date(dataStartDate + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}</span>
          <span>{daysRemaining} days remaining</span>
        </div>
        <div className="h-2 bg-bg rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-1000"
            style={{
              width: `${progress}%`,
              background: `linear-gradient(90deg, ${colors.info}, ${colors.profit})`,
            }}
          />
        </div>
      </div>

      {/* Feature preview cards */}
      <div className="w-full max-w-2xl">
        <div className="text-xs text-text-dim uppercase tracking-widest text-center mb-3">
          What you'll be able to do
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <FeatureCard
            icon={Shield}
            title="Stop Buffer Tuning"
            desc="Test different call/put stop buffers against real spread data. See exactly which stops would be avoided."
          />
          <FeatureCard
            icon={Database}
            title="Credit Gate Analysis"
            desc="Adjust minimum credit thresholds. See which entries would be skipped or converted to one-sided."
          />
          <FeatureCard
            icon={TrendingUp}
            title="Equity Curve Comparison"
            desc="Overlay actual vs simulated equity curves. Visualize the P&L impact of every change."
          />
          <FeatureCard
            icon={Gauge}
            title="Risk Metrics"
            desc="Compare Sharpe ratio, max drawdown, win rate, and avg P&L side by side."
          />
        </div>
      </div>

      {/* Data collection log */}
      {allDates.length > 0 && (
        <div className="w-full max-w-2xl mt-6">
          <div className="text-xs text-text-dim uppercase tracking-widest text-center mb-2">
            Data collected
          </div>
          <div className="flex flex-wrap gap-1.5 justify-center">
            {allDates.map((d) => (
              <div
                key={d}
                className="px-2 py-0.5 rounded text-[10px] font-mono"
                style={{
                  backgroundColor: `${colors.profit}15`,
                  color: colors.profit,
                  border: `1px solid ${colors.profit}30`,
                }}
              >
                {d.slice(5)}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
