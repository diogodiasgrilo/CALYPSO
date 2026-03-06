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
import { useHydraStore } from "../../store/hydraStore";
import { colors } from "../../lib/tradingColors";

export function SPXChart() {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLinesRef = useRef<ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]>[]>([]);

  const { todayOHLC, hydraState } = useHydraStore();

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

    // Parse timestamp as ET (database stores ET timestamps without timezone suffix).
    // Determine EDT vs EST from the date (DST: second Sunday Mar – first Sunday Nov).
    function parseET(ts: string): number {
      const d = new Date(ts);
      if (isNaN(d.getTime())) return 0;
      // Get local offset in minutes, compute ET offset
      const localOffsetMin = d.getTimezoneOffset(); // positive = west of UTC
      const month = d.getMonth(); // 0-indexed
      // Rough DST check: Mar-Nov = EDT (UTC-4), otherwise EST (UTC-5)
      const etOffsetMin = (month >= 2 && month <= 10) ? 240 : 300; // EDT=240, EST=300
      // new Date() parsed as local; convert to UTC, then to ET epoch
      const utcMs = d.getTime() + localOffsetMin * 60000;
      const etMs = utcMs - etOffsetMin * 60000;
      // We want the chart to show ET times, so return ET epoch as if it were UTC
      // (Lightweight Charts displays the raw timestamp)
      return etMs / 1000;
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
        position: "belowBar" as const,
        color:
          e.call_side_stopped || e.put_side_stopped
            ? colors.loss
            : colors.info,
        shape: "arrowUp" as const,
        text: `E${e.entry_number}`,
      }));

    if (markers.length > 0) {
      createSeriesMarkers(candleSeriesRef.current, markers);
    }

    // Remove old price lines before adding new ones
    const series = candleSeriesRef.current;
    for (const line of priceLinesRef.current) {
      series.removePriceLine(line);
    }
    priceLinesRef.current = [];

    // Add price lines for active entries
    entries.forEach((e) => {
      const isActive = e.is_complete && !e.call_side_stopped && !e.put_side_stopped && !e.call_side_expired && !e.put_side_expired;
      if (isActive && e.short_call_strike > 0) {
        const line = series.createPriceLine({
          price: e.short_call_strike,
          color: colors.loss + "80",
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: `SC${e.entry_number}`,
        });
        priceLinesRef.current.push(line);
      }
      if (isActive && e.short_put_strike > 0) {
        const line = series.createPriceLine({
          price: e.short_put_strike,
          color: colors.loss + "80",
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: `SP${e.entry_number}`,
        });
        priceLinesRef.current.push(line);
      }
    });

    // Scroll to latest
    chartRef.current?.timeScale().scrollToRealTime();
  }, [todayOHLC, hydraState?.entries]);

  return (
    <div>
      <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-2">
        SPX 1-Min
      </h3>
      <div
        ref={containerRef}
        className="rounded-lg border border-border-dim overflow-hidden"
        style={{ height: 300 }}
      />
    </div>
  );
}
