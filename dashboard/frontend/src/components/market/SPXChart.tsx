import { useEffect, useRef, useMemo } from "react";
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
import { useHydraStore, type HydraEntry } from "../../store/hydraStore";
import { colors } from "../../lib/tradingColors";

/** Parse ET timestamp → epoch seconds (Lightweight Charts renders as-if-UTC → shows ET labels). */
function parseET(ts: string): number {
  const m = ts.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/);
  if (!m) return 0;
  const utcDate = new Date(`${m[1]}T${m[2]}Z`);
  return isNaN(utcDate.getTime()) ? 0 : utcDate.getTime() / 1000;
}

/** Stable hash of entry fields relevant to markers/price lines. */
function entriesHash(entries: HydraEntry[]): string {
  return entries.map(e =>
    `${e.entry_number}|${e.entry_time}|${e.call_side_stopped}|${e.put_side_stopped}|${e.call_side_expired}|${e.put_side_expired}|${e.call_side_skipped}|${e.put_side_skipped}|${e.short_call_strike}|${e.short_put_strike}`
  ).join("~");
}

export function SPXChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLinesRef = useRef<ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]>[]>([]);
  const prevEntriesHashRef = useRef("");
  const prevStopCountRef = useRef(0);
  const prevShowStrikesRef = useRef(false);

  const todayOHLC = useHydraStore((s) => s.todayOHLC);
  const hydraEntries = useHydraStore((s) => s.hydraState?.entries);
  const stopEvents = useHydraStore((s) => s.stopEvents);
  const showStrikes = useHydraStore((s) => s.showStrikes);
  const toggleStrikes = useHydraStore((s) => s.toggleStrikes);

  const entries = useMemo(() => hydraEntries ?? [], [hydraEntries]);

  // Create chart on mount
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: colors.card },
        textColor: colors.textSecondary,
        fontFamily: "Inter, 'SF Mono', 'Fira Code', monospace",
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
    const observer = new ResizeObserver((resizeEntries) => {
      for (const entry of resizeEntries) {
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

  // Update candlestick data when OHLC changes
  useEffect(() => {
    if (!candleSeriesRef.current || todayOHLC.length === 0) return;

    const data = todayOHLC.map((bar) => ({
      time: parseET(bar.timestamp) as Time,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    }));

    candleSeriesRef.current.setData(data);
    chartRef.current?.timeScale().scrollToRealTime();
  }, [todayOHLC]);

  // Update markers and price lines only when entries/stops actually change
  useEffect(() => {
    if (!candleSeriesRef.current) return;

    const currentHash = entriesHash(entries);
    const currentStopCount = stopEvents.length;

    // Skip if nothing changed (OHLC updates won't trigger marker rebuild)
    const strikesChanged = showStrikes !== prevShowStrikesRef.current;
    if (currentHash === prevEntriesHashRef.current && currentStopCount === prevStopCountRef.current && !strikesChanged) {
      return;
    }
    prevEntriesHashRef.current = currentHash;
    prevStopCountRef.current = currentStopCount;
    prevShowStrikesRef.current = showStrikes;

    // Build entry markers (exclude fully-skipped entries where both sides were never placed)
    const markers = entries
      .filter((e) => e.entry_time && !isNaN(new Date(e.entry_time).getTime()) && !(e.call_side_skipped && e.put_side_skipped))
      .map((e) => ({
        time: parseET(e.entry_time!) as Time,
        position: "aboveBar" as const,
        color:
          e.call_side_stopped && e.put_side_stopped
            ? colors.loss
            : e.call_side_stopped || e.put_side_stopped
              ? colors.warning
              : colors.info,
        shape: "arrowDown" as const,
        text: `E${e.entry_number}`,
      }));

    // Build stop markers
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

    // Update price lines
    const series = candleSeriesRef.current;
    for (const line of priceLinesRef.current) {
      series.removePriceLine(line);
    }
    priceLinesRef.current = [];

    if (showStrikes) {
      entries.forEach((e) => {
        if (!e.entry_time) return;
        const isActive = !e.call_side_stopped && !e.put_side_stopped && !e.call_side_expired && !e.put_side_expired;
        // Active entries: solid red. Expired/stopped: dimmer, dotted.
        const lineColor = isActive ? colors.loss : colors.textDim;
        const lineStyle = isActive ? 2 : 3; // 2=dashed, 3=dotted

        if (e.short_call_strike > 0 && !e.call_side_skipped) {
          const line = series.createPriceLine({
            price: e.short_call_strike,
            color: lineColor,
            lineWidth: 1,
            lineStyle,
            axisLabelVisible: isActive,
            axisLabelColor: lineColor,
            title: `SC${e.entry_number}`,
          });
          priceLinesRef.current.push(line);
        }
        if (e.short_put_strike > 0 && !e.put_side_skipped) {
          const line = series.createPriceLine({
            price: e.short_put_strike,
            color: lineColor,
            lineWidth: 1,
            lineStyle,
            axisLabelVisible: isActive,
            axisLabelColor: lineColor,
            title: `SP${e.entry_number}`,
          });
          priceLinesRef.current.push(line);
        }
      });
    }
  }, [entries, stopEvents, showStrikes]);

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h3 className="label-upper">SPX 1-Min</h3>
        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showStrikes}
            onChange={toggleStrikes}
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
