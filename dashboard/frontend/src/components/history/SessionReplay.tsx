import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { Play, Pause, RotateCcw } from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import { colors } from "../../lib/tradingColors";
// colors used by charts below

interface Tick {
  timestamp: string;
  spx_price: number;
  vix_level: number;
  bot_state?: string;
}

interface ReplayEntry {
  entry_number: number;
  entry_time: string;
  total_credit: number;
  short_call_strike: number;
  short_put_strike: number;
}

type ReplayState = "idle" | "loading" | "ready" | "playing" | "paused" | "complete";

const SPEEDS = [1, 2, 5, 10] as const;

interface SessionReplayProps {
  date: string;
}

export function SessionReplay({ date }: SessionReplayProps) {
  const [state, setState] = useState<ReplayState>("idle");
  const [ticks, setTicks] = useState<Tick[]>([]);
  const [entries, setEntries] = useState<ReplayEntry[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load data
  useEffect(() => {
    setState("loading");
    Promise.all([
      fetch(`/api/market/ticks?date_str=${date}`).then((r) => r.json()),
      fetch(`/api/hydra/entries?date_str=${date}`).then((r) => r.json()),
    ])
      .then(([tickData, entryData]) => {
        const t = (tickData.ticks ?? tickData ?? []) as Tick[];
        setTicks(t);
        // Normalize entries: DB has total_credit, state file has per-side credits
        const rawEntries = (entryData.entries ?? []) as Record<string, unknown>[];
        setEntries(rawEntries.map((e) => ({
          entry_number: (e.entry_number ?? 0) as number,
          entry_time: (e.entry_time ?? "") as string,
          total_credit: (e.total_credit ?? ((e.call_spread_credit as number ?? 0) + (e.put_spread_credit as number ?? 0))) as number,
          short_call_strike: (e.short_call_strike ?? 0) as number,
          short_put_strike: (e.short_put_strike ?? 0) as number,
        })));
        setCurrentIndex(0);
        setState(t.length > 0 ? "ready" : "idle");
      })
      .catch(() => setState("idle"));
  }, [date]);

  // Cleanup interval
  useEffect(() => {
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  const currentTick = ticks[currentIndex] ?? null;

  // Entries visible up to current time
  const visibleEntries = useMemo(() => {
    if (!currentTick) return [];
    return entries.filter((e) => e.entry_time <= currentTick.timestamp);
  }, [entries, currentTick]);

  // P&L curve data up to current index — incremental O(n) approach
  const pnlData = useMemo(() => {
    if (ticks.length === 0 || entries.length === 0) return [];
    // Pre-sort entries by time for a single-pass scan
    const sorted = [...entries].sort((a, b) => a.entry_time.localeCompare(b.entry_time));
    let entryIdx = 0;
    let runningCredit = 0;
    const data: { time: string; pnl: number }[] = [];
    for (let i = 0; i <= currentIndex && i < ticks.length; i++) {
      const t = ticks[i];
      // Advance through sorted entries that are now visible
      while (entryIdx < sorted.length && sorted[entryIdx].entry_time <= t.timestamp) {
        runningCredit += sorted[entryIdx].total_credit;
        entryIdx++;
      }
      data.push({ time: t.timestamp.slice(11, 16), pnl: runningCredit });
    }
    return data;
  }, [ticks, entries, currentIndex]);

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

  // Update interval when speed changes during playback
  useEffect(() => {
    if (state === "playing") {
      play();
    }
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

        {/* Speed selector */}
        <div className="flex gap-1">
          {SPEEDS.map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                speed === s
                  ? "bg-info/20 text-info"
                  : "text-text-dim hover:text-text-secondary"
              }`}
            >
              {s}x
            </button>
          ))}
        </div>

        {/* Time display */}
        <span className="text-xs font-mono text-text-primary ml-auto">{timeLabel}</span>
      </div>

      {/* Progress bar / scrubber */}
      <div className="relative">
        <input
          type="range"
          min={0}
          max={ticks.length - 1}
          value={currentIndex}
          onChange={(e) => {
            const idx = Number(e.target.value);
            setCurrentIndex(idx);
            if (state === "playing" && intervalRef.current) {
              clearInterval(intervalRef.current);
            }
            setState("paused");
          }}
          className="w-full h-1.5 appearance-none bg-bg-elevated rounded-full cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-info"
        />
      </div>

      {/* SPX + VIX display */}
      {currentTick && (
        <div className="flex gap-4 text-xs">
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
          <div className="ml-auto text-text-dim">
            {currentTick.bot_state ?? ""}
          </div>
        </div>
      )}

      {/* Mini P&L curve */}
      {pnlData.length > 1 && (
        <div className="bg-card rounded-lg border border-border-dim p-2">
          <ResponsiveContainer width="100%" height={100}>
            <AreaChart data={pnlData}>
              <XAxis
                dataKey="time"
                tick={{ fontSize: 9, fill: colors.textDim }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis hide />
              <ReferenceLine y={0} stroke={colors.textDim} strokeDasharray="3 3" />
              <Area
                type="monotone"
                dataKey="pnl"
                stroke={colors.profit}
                fill={colors.profit}
                fillOpacity={0.15}
                strokeWidth={1.5}
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
                <span className="text-text-dim">{e.entry_time.slice(11, 16)}</span>
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
