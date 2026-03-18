import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { Play, Pause, RotateCcw } from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { colors } from "../../lib/tradingColors";

interface Tick {
  timestamp: string;
  spx_price: number;
  vix_level: number;
  bot_state?: string;
}

interface ReplayEntry {
  entry_number: number;
  entry_time: string;
  entry_time_24h: string;
  total_credit: number;
  short_call_strike: number;
  short_put_strike: number;
}

interface PnLPoint {
  time: string; // "HH:MM"
  pnl: number;
}

type ReplayState = "idle" | "loading" | "ready" | "playing" | "paused" | "complete";

const SPEEDS = [1, 2, 5, 10] as const;

/**
 * Extract 24h "HH:MM:SS" from any entry_time format.
 */
function toTime24h(ts: string): string {
  if (!ts) return "00:00:00";
  const ampm = ts.match(/(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)/i);
  if (ampm) {
    let h = parseInt(ampm[1], 10);
    const min = ampm[2];
    const sec = ampm[3] ?? "00";
    const period = ampm[4].toUpperCase();
    if (period === "PM" && h !== 12) h += 12;
    if (period === "AM" && h === 12) h = 0;
    return `${String(h).padStart(2, "0")}:${min}:${sec}`;
  }
  const full = ts.match(/\d{4}-\d{2}-\d{2}[T ](\d{2}:\d{2}:\d{2})/);
  if (full) return full[1];
  const bare = ts.match(/^(\d{2}:\d{2}(?::\d{2})?)$/);
  if (bare) return bare[1].length === 5 ? bare[1] + ":00" : bare[1];
  return "00:00:00";
}

function tickTime(ts: string): string {
  return ts.slice(11, 19);
}

function fmtEntryTime(ts: string): string {
  return toTime24h(ts).slice(0, 5);
}

export function SessionReplay({ date }: { date: string }) {
  const [state, setState] = useState<ReplayState>("idle");
  const [ticks, setTicks] = useState<Tick[]>([]);
  const [entries, setEntries] = useState<ReplayEntry[]>([]);
  const [pnlCurve, setPnlCurve] = useState<PnLPoint[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load all data in parallel
  useEffect(() => {
    setState("loading");
    Promise.all([
      fetch(`/api/market/ticks?date_str=${date}`).then((r) => r.json()),
      fetch(`/api/hydra/entries?date_str=${date}`).then((r) => r.json()),
      fetch(`/api/market/replay_pnl?date_str=${date}`).then((r) => r.json()),
    ])
      .then(([tickData, entryData, pnlData]) => {
        const t = (tickData.ticks ?? []) as Tick[];
        setTicks(t);

        const rawEntries = (entryData.entries ?? []) as Record<string, unknown>[];
        setEntries(rawEntries.map((e) => {
          const rawTime = (e.entry_time ?? "") as string;
          return {
            entry_number: (e.entry_number ?? 0) as number,
            entry_time: rawTime,
            entry_time_24h: toTime24h(rawTime),
            total_credit: (e.total_credit ?? ((e.call_spread_credit as number ?? 0) + (e.put_spread_credit as number ?? 0))) as number,
            short_call_strike: (e.short_call_strike ?? 0) as number,
            short_put_strike: (e.short_put_strike ?? 0) as number,
          };
        }));

        setPnlCurve((pnlData.pnl_curve ?? []) as PnLPoint[]);
        setCurrentIndex(0);
        setState(t.length > 0 ? "ready" : "idle");
      })
      .catch(() => setState("idle"));
  }, [date]);

  useEffect(() => {
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, []);

  const currentTick = ticks[currentIndex] ?? null;

  // Entries visible up to current time
  const visibleEntries = useMemo(() => {
    if (!currentTick) return [];
    const now = tickTime(currentTick.timestamp);
    return entries.filter((e) => e.entry_time_24h <= now);
  }, [entries, currentTick]);

  // Slice the real P&L curve up to current tick time
  const visiblePnl = useMemo(() => {
    if (pnlCurve.length === 0 || !currentTick) return [];
    const nowMinute = currentTick.timestamp.slice(11, 16);
    // Find last point <= current time
    let endIdx = 0;
    for (let i = 0; i < pnlCurve.length; i++) {
      if (pnlCurve[i].time <= nowMinute) endIdx = i + 1;
      else break;
    }
    return pnlCurve.slice(0, endIdx);
  }, [pnlCurve, currentTick]);

  // Current P&L value for display
  const currentPnl = visiblePnl.length > 0 ? visiblePnl[visiblePnl.length - 1].pnl : 0;

  // Y-axis domain: symmetric around 0 or fit data
  const yDomain = useMemo(() => {
    if (pnlCurve.length === 0) return [-100, 100];
    const allVals = pnlCurve.map((p) => p.pnl);
    const max = Math.max(...allVals, 0);
    const min = Math.min(...allVals, 0);
    const pad = Math.max(Math.abs(max), Math.abs(min)) * 0.15 + 50;
    return [Math.floor(min - pad), Math.ceil(max + pad)];
  }, [pnlCurve]);

  const play = useCallback(() => {
    if (ticks.length === 0) return;
    setState("playing");
    if (intervalRef.current) clearInterval(intervalRef.current);
    intervalRef.current = setInterval(() => {
      setCurrentIndex((prev) => {
        if (prev >= ticks.length - 1) {
          if (intervalRef.current) clearInterval(intervalRef.current);
          setState("complete");
          return prev;
        }
        return prev + 1;
      });
    }, 100 / speed);
  }, [ticks.length, speed]);

  const pause = useCallback(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    setState("paused");
  }, []);

  const reset = useCallback(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    setCurrentIndex(0);
    setState("ready");
  }, []);

  useEffect(() => {
    if (state === "playing") play();
  }, [speed, play]);

  const timeLabel = currentTick?.timestamp?.slice(11, 19) ?? "--:--:--";

  if (state === "idle") {
    return (
      <div className="text-center text-text-dim text-xs py-8">
        No tick data available for replay
      </div>
    );
  }

  if (state === "loading") {
    return (
      <div className="text-center text-text-dim text-xs py-8">
        Loading replay data...
      </div>
    );
  }

  const pnlColor = currentPnl >= 0 ? colors.profit : colors.loss;

  return (
    <div className="space-y-3">
      {/* Controls */}
      <div className="flex items-center gap-3">
        {state === "playing" ? (
          <button onClick={pause} className="p-1.5 rounded bg-bg-elevated text-text-primary hover:bg-card-hover">
            <Pause size={14} />
          </button>
        ) : (
          <button onClick={play} className="p-1.5 rounded bg-bg-elevated text-text-primary hover:bg-card-hover">
            <Play size={14} />
          </button>
        )}
        <button onClick={reset} className="p-1.5 rounded bg-bg-elevated text-text-secondary hover:text-text-primary">
          <RotateCcw size={14} />
        </button>

        <div className="flex gap-1">
          {SPEEDS.map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                speed === s ? "bg-info/20 text-info" : "text-text-dim hover:text-text-secondary"
              }`}
            >
              {s}x
            </button>
          ))}
        </div>

        <span className="text-xs font-mono text-text-primary ml-auto">{timeLabel}</span>
      </div>

      {/* Scrubber */}
      <input
        type="range"
        min={0}
        max={ticks.length - 1}
        value={currentIndex}
        onChange={(e) => {
          setCurrentIndex(Number(e.target.value));
          if (state === "playing" && intervalRef.current) clearInterval(intervalRef.current);
          setState("paused");
        }}
        className="w-full h-1.5 appearance-none bg-bg-elevated rounded-full cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-info"
      />

      {/* SPX / VIX / P&L display */}
      {currentTick && (
        <div className="flex gap-4 text-xs items-baseline">
          <div>
            <span className="text-text-secondary mr-1">SPX</span>
            <span className="text-text-primary font-semibold">
              {currentTick.spx_price?.toFixed(2) ?? "--"}
            </span>
          </div>
          <div>
            <span className="text-text-secondary mr-1">VIX</span>
            <span className="text-text-primary font-semibold">
              {currentTick.vix_level?.toFixed(1) ?? "--"}
            </span>
          </div>
          {visiblePnl.length > 0 && (
            <div>
              <span className="text-text-secondary mr-1">P&L</span>
              <span className="font-semibold" style={{ color: pnlColor }}>
                {currentPnl >= 0 ? "+" : ""}${currentPnl.toFixed(0)}
              </span>
            </div>
          )}
          <div className="ml-auto text-text-dim">
            {currentTick.bot_state ?? ""}
          </div>
        </div>
      )}

      {/* Real P&L curve from spread_snapshots */}
      {visiblePnl.length > 1 && (
        <div className="bg-card rounded-lg border border-border-dim p-2">
          <ResponsiveContainer width="100%" height={140}>
            <AreaChart data={visiblePnl}>
              <defs>
                <linearGradient id="pnlGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={pnlColor} stopOpacity={0.25} />
                  <stop offset="100%" stopColor={pnlColor} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="time"
                tick={{ fontSize: 9, fill: colors.textDim }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                width={45}
                domain={yDomain}
                tick={{ fontSize: 9, fill: colors.textDim }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v: number) => `$${v}`}
              />
              <ReferenceLine y={0} stroke={colors.textDim} strokeDasharray="3 3" />
              <Tooltip
                contentStyle={{
                  backgroundColor: colors.card,
                  border: `1px solid ${colors.borderDim}`,
                  borderRadius: 6,
                  fontSize: 11,
                }}
                labelStyle={{ color: colors.textSecondary }}
                formatter={(value: number | undefined) => [`$${(value ?? 0).toFixed(2)}`, "P&L"]}
              />
              <Area
                type="monotone"
                dataKey="pnl"
                stroke={pnlColor}
                fill="url(#pnlGradient)"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Visible entries */}
      {visibleEntries.length > 0 && (
        <div className="grid grid-cols-3 gap-2 max-sm:grid-cols-2">
          {visibleEntries.map((e) => (
            <div
              key={e.entry_number}
              className="bg-bg-elevated rounded p-2 text-xs border border-border-dim"
            >
              <div className="flex justify-between mb-1">
                <span className="font-semibold text-text-primary">E{e.entry_number}</span>
                <span className="text-text-dim">{fmtEntryTime(e.entry_time)}</span>
              </div>
              <div className="flex justify-between text-text-secondary">
                <span>C:{e.short_call_strike}</span>
                <span>P:{e.short_put_strike}</span>
              </div>
              <div className="text-right mt-0.5" style={{ color: colors.profit }}>
                ${(e.total_credit ?? 0).toFixed(2)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
