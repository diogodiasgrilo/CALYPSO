import { useEffect, useRef } from "react";
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  type IChartApi,
  type ISeriesApi,
  type Time,
  ColorType,
  CrosshairMode,
} from "lightweight-charts";
import { colors } from "../../lib/tradingColors";
import type { OHLCBar, DayEntry, DayStop } from "./types";

/**
 * Parse ET timestamp to epoch seconds.
 * Handles "2026-03-06 12:15:00" (bare) and "2026-03-06T11:15:32-05:00" (ISO).
 * Returns epoch-as-if-UTC so chart axis shows ET labels.
 */
function parseET(ts: string): number {
  const m = ts.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/);
  if (!m) return 0;
  const utcDate = new Date(`${m[1]}T${m[2]}Z`);
  return isNaN(utcDate.getTime()) ? 0 : utcDate.getTime() / 1000;
}

/**
 * Parse a time-only string like "10:15 AM ET" into epoch seconds for a given date.
 * Falls back to parseET for full timestamps.
 */
function parseTimeForDate(ts: string, dateStr: string): number {
  if (!ts) return 0;

  // Try full timestamp first: "2026-03-06 12:15:00" or ISO
  const full = parseET(ts);
  if (full > 0) return full;

  // Parse "HH:MM AM/PM" or "HH:MM AM ET" format
  const ampm = ts.match(/(\d{1,2}):(\d{2})\s*(AM|PM)/i);
  if (ampm) {
    let h = parseInt(ampm[1], 10);
    const min = parseInt(ampm[2], 10);
    const period = ampm[3].toUpperCase();
    if (period === "PM" && h !== 12) h += 12;
    if (period === "AM" && h === 12) h = 0;
    const utcDate = new Date(`${dateStr}T${String(h).padStart(2, "0")}:${String(min).padStart(2, "0")}:00Z`);
    return isNaN(utcDate.getTime()) ? 0 : utcDate.getTime() / 1000;
  }

  // Try bare "HH:MM" or "HH:MM:SS"
  const bare = ts.match(/^(\d{2}:\d{2}(:\d{2})?)/);
  if (bare) {
    const timeStr = bare[1].length === 5 ? bare[1] + ":00" : bare[1];
    const utcDate = new Date(`${dateStr}T${timeStr}Z`);
    return isNaN(utcDate.getTime()) ? 0 : utcDate.getTime() / 1000;
  }

  return 0;
}

export function DayDetailChart({
  date,
  bars,
  entries,
  stops,
}: {
  date: string;
  bars: OHLCBar[];
  entries: DayEntry[];
  stops: DayStop[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  // Create chart on mount
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: colors.card },
        textColor: colors.textSecondary,
        fontFamily: "'SF Mono', 'Fira Code', monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: colors.borderDim },
        horzLines: { color: colors.borderDim },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: colors.textDim, width: 1, style: 2 },
        horzLine: { color: colors.textDim, width: 1, style: 2 },
      },
      rightPriceScale: {
        borderColor: colors.borderDim,
        scaleMargins: { top: 0.12, bottom: 0.22 },
      },
      timeScale: {
        borderColor: colors.borderDim,
        timeVisible: true,
        secondsVisible: false,
        barSpacing: 4,
      },
      handleScroll: { vertTouchDrag: false },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: colors.profit,
      downColor: colors.loss,
      borderUpColor: colors.profit,
      borderDownColor: colors.loss,
      wickUpColor: colors.profitMuted,
      wickDownColor: colors.lossMuted,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        chart.applyOptions({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        });
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
    };
  }, []);

  // Update data
  useEffect(() => {
    if (!candleSeriesRef.current || bars.length === 0) return;

    const data = bars.map((bar) => ({
      time: parseET(bar.timestamp) as Time,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    }));

    candleSeriesRef.current.setData(data);

    // Build stop lookup: entry_number → set of stopped sides
    const stopMap = new Map<number, Set<string>>();
    for (const s of stops) {
      if (!stopMap.has(s.entry_number)) stopMap.set(s.entry_number, new Set());
      stopMap.get(s.entry_number)!.add(s.side);
    }

    // Entry markers
    const entryMarkers = entries
      .filter((e) => e.entry_time)
      .map((e) => {
        const stoppedSides = stopMap.get(e.entry_number);
        const stoppedCount = stoppedSides?.size ?? 0;
        const t = parseTimeForDate(e.entry_time, date);
        return {
          time: t as Time,
          position: "aboveBar" as const,
          color:
            stoppedCount >= 2
              ? colors.loss
              : stoppedCount === 1
              ? colors.warning
              : colors.info,
          shape: "arrowDown" as const,
          text: `E${e.entry_number}`,
        };
      })
      .filter((m) => (m.time as number) > 0);

    // Stop markers — individual marker per stop on its correct candle
    const stopMarkers = stops
      .filter((s) => s.stop_time)
      .map((s) => {
        const t = parseTimeForDate(s.stop_time, date);
        const sideChar = s.side === "call" ? "C" : "P";
        return {
          time: t as Time,
          position: "belowBar" as const,
          color: colors.loss,
          shape: "circle" as const,
          text: `S${s.entry_number}${sideChar}`,
        };
      })
      .filter((m) => (m.time as number) > 0);

    const allMarkers = [...entryMarkers, ...stopMarkers].sort(
      (a, b) => (a.time as number) - (b.time as number)
    );

    if (allMarkers.length > 0) {
      createSeriesMarkers(candleSeriesRef.current, allMarkers);
    }

    // Fit full day
    chartRef.current?.timeScale().fitContent();
  }, [date, bars, entries, stops]);

  if (bars.length === 0) {
    return (
      <div className="flex items-center justify-center h-40 text-text-dim text-xs rounded-lg border border-border-dim">
        No OHLC data available for this date
      </div>
    );
  }

  return (
    <div>
      <h4 className="text-[11px] font-semibold text-text-secondary uppercase tracking-wider mb-2">
        SPX 1-Min Chart
      </h4>
      <div
        ref={containerRef}
        className="rounded-lg border border-border-dim overflow-hidden"
        style={{ height: 320 }}
      />
    </div>
  );
}
