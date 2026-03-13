import { useEffect, useRef, useState } from "react";
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
import { useHydraStore } from "../../store/hydraStore";
import { colors } from "../../lib/tradingColors";

export function SPXChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLinesRef = useRef<ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]>[]>([]);

  const { todayOHLC, hydraState, stopEvents } = useHydraStore();
  const [showStrikes, setShowStrikes] = useState(false);

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
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: colors.borderDim,
        timeVisible: true,
        secondsVisible: false,
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

    // Handle resize
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

  // Update data when OHLC changes
  useEffect(() => {
    if (!candleSeriesRef.current || todayOHLC.length === 0) return;

    // Parse timestamp and return epoch seconds that Lightweight Charts will display as ET.
    // Handles two formats:
    //   OHLC: "2026-03-06 12:15:00" (bare ET, no timezone suffix)
    //   Entry: "2026-03-06T11:15:32.014246-05:00" (ISO with timezone offset)
    // In BOTH cases the date+time digits ARE the ET wall clock time, so we extract
    // them and return epoch-as-if-UTC so the chart axis shows ET labels.
    function parseET(ts: string): number {
      const m = ts.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/);
      if (!m) return 0;
      const utcDate = new Date(`${m[1]}T${m[2]}Z`);
      return isNaN(utcDate.getTime()) ? 0 : utcDate.getTime() / 1000;
    }

    const data = todayOHLC.map((bar) => ({
      time: parseET(bar.timestamp) as Time,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    }));

    candleSeriesRef.current.setData(data);

    // Add entry markers via v5 primitive
    const entries = hydraState?.entries ?? [];
    const markers = entries
      .filter((e) => e.entry_time && !isNaN(new Date(e.entry_time).getTime()))
      .map((e) => ({
        time: parseET(e.entry_time!) as Time,
        position: "aboveBar" as const,
        color:
          e.call_side_stopped && e.put_side_stopped
            ? colors.loss // double stop = red
            : e.call_side_stopped || e.put_side_stopped
              ? colors.warning // single stop = amber
              : colors.info, // active = blue
        shape: "arrowDown" as const,
        text: `E${e.entry_number}`,
      }));

    // Add stop markers (red circles below bar)
    const stopMarkers = stopEvents
      .filter((s) => s.stop_time)
      .map((s) => ({
        time: parseET(s.stop_time) as Time,
        position: "belowBar" as const,
        color: colors.loss,
        shape: "circle" as const,
        text: `S${s.entry_number}${s.side === "call" ? "C" : "P"}`,
      }))
      .filter((m) => (m.time as number) > 0);

    const allMarkers = [...markers, ...stopMarkers].sort(
      (a, b) => (a.time as number) - (b.time as number)
    );

    if (allMarkers.length > 0) {
      createSeriesMarkers(candleSeriesRef.current, allMarkers);
    }

    // Remove old price lines before adding new ones
    const series = candleSeriesRef.current;
    for (const line of priceLinesRef.current) {
      series.removePriceLine(line);
    }
    priceLinesRef.current = [];

    // Add price lines for active entries (only when toggle is on)
    if (showStrikes) {
      entries.forEach((e) => {
        const isActive = !!e.entry_time && !e.call_side_stopped && !e.put_side_stopped && !e.call_side_expired && !e.put_side_expired;
        if (isActive && e.short_call_strike > 0) {
          const line = series.createPriceLine({
            price: e.short_call_strike,
            color: colors.loss,
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            axisLabelColor: colors.loss,
            title: `SC${e.entry_number}`,
          });
          priceLinesRef.current.push(line);
        }
        if (isActive && e.short_put_strike > 0) {
          const line = series.createPriceLine({
            price: e.short_put_strike,
            color: colors.loss,
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            axisLabelColor: colors.loss,
            title: `SP${e.entry_number}`,
          });
          priceLinesRef.current.push(line);
        }
      });
    }

    // Scroll to latest
    chartRef.current?.timeScale().scrollToRealTime();
  }, [todayOHLC, hydraState?.entries, stopEvents, showStrikes]);

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">
          SPX 1-Min
        </h3>
        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showStrikes}
            onChange={(e) => setShowStrikes(e.target.checked)}
            className="w-3 h-3 rounded accent-loss cursor-pointer"
          />
          <span className="text-[10px] text-text-dim">Show Strikes</span>
        </label>
      </div>
      <div
        ref={containerRef}
        className="rounded-lg border border-border-dim overflow-hidden"
        style={{ height: 300 }}
      />
    </div>
  );
}
