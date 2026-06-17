import { useEffect, useRef, useMemo, useState } from "react";
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type Time,
  ColorType,
  CrosshairMode,
} from "lightweight-charts";
import { useHydraStore, type HydraEntry, type OHLCBar } from "../../store/hydraStore";
import { colors } from "../../lib/tradingColors";

type SeriesType = "candle" | "line";
// Generic series handle — markers + price lines work on both candle and line.
type AnySeries = ISeriesApi<"Candlestick"> | ISeriesApi<"Line">;

/** Decide how to draw the price track from the data's density.
 *  Candlesticks need several samples/minute to show a body+wick; a feed that
 *  samples ~1×/min produces all-doji bars (open=high=low=close) that render as
 *  ugly flat crosses. When most bars are degenerate we draw a clean line of
 *  closes instead — honest and continuous at any sampling rate. Dense feeds
 *  (e.g. variant A at ~4-8×/min) keep real candlesticks. */
function chooseSeriesType(bars: OHLCBar[]): SeriesType {
  if (bars.length < 5) return "candle";
  const dojis = bars.filter(
    (b) => b.high === b.low && b.open === b.close && b.high === b.open
  ).length;
  return dojis / bars.length > 0.5 ? "line" : "candle";
}

function makeSeries(chart: IChartApi, type: SeriesType): AnySeries {
  if (type === "line") {
    return chart.addSeries(LineSeries, {
      color: colors.info,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });
  }
  return chart.addSeries(CandlestickSeries, {
    upColor: colors.profit,
    downColor: colors.loss,
    borderUpColor: colors.profit,
    borderDownColor: colors.loss,
    wickUpColor: colors.profitMuted,
    wickDownColor: colors.lossMuted,
  });
}

/** Format a chart Time (epoch that ENCODES the ET wall-clock as-UTC, produced by
 *  parseET) as HH:MM in Eastern, regardless of the viewer's browser timezone.
 *  Lightweight Charts otherwise renders tick/crosshair labels in the browser's
 *  LOCAL zone; reading the UTC components of the as-UTC epoch returns the
 *  original ET wall-clock, so the market clock shows correctly everywhere. */
function fmtChartTimeET(time: Time): string {
  const d = new Date((time as number) * 1000);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

/** Parse an ET timestamp → epoch seconds that ENCODES the ET wall-clock as-UTC
 *  (display is forced to ET by fmtChartTimeET via the chart's tickMark/time
 *  formatters — do NOT rely on Lightweight Charts' default local rendering).
 *  Handles full ISO ("2026-04-07T12:03:01-04:00"), datetime ("2026-04-07 12:03:01"),
 *  and time-only ("12:03:08") formats. Time-only uses today's date. */
function parseET(ts: string, fallbackDate?: string): number {
  // Full date+time: "2026-04-07T12:03:01..." or "2026-04-07 12:03:01"
  const m = ts.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/);
  if (m) {
    const utcDate = new Date(`${m[1]}T${m[2]}Z`);
    return isNaN(utcDate.getTime()) ? 0 : utcDate.getTime() / 1000;
  }
  // Time-only: "12:03:08" — use fallbackDate or today
  const t = ts.match(/^(\d{2}:\d{2}:\d{2})/);
  if (t) {
    const dateStr = fallbackDate || new Date().toISOString().slice(0, 10);
    const utcDate = new Date(`${dateStr}T${t[1]}Z`);
    return isNaN(utcDate.getTime()) ? 0 : utcDate.getTime() / 1000;
  }
  return 0;
}

/** Stable hash of entry fields relevant to markers/price lines. */
function entriesHash(entries: HydraEntry[]): string {
  return entries.map(e =>
    `${e.entry_number}|${e.entry_time}|${e.call_side_stopped}|${e.put_side_stopped}|${e.call_side_expired}|${e.put_side_expired}|${e.call_side_skipped}|${e.put_side_skipped}|${e.short_call_strike}|${e.short_put_strike}`
  ).join("~");
}

/** Derive belowBar stop markers from entries' per-side *_stop_time fields.
 *  Used in the polled (non-primary) path where there is no WS stopEvents stream;
 *  the snapshot entries carry the stop times directly. The primary path still
 *  uses the live WS stopEvents (richer, transition-detected). */
function stopEventsFromEntries(entries: HydraEntry[]): StopEventLike[] {
  const out: StopEventLike[] = [];
  for (const e of entries) {
    if (e.call_side_stopped && e.call_stop_time) {
      out.push({ entry_number: e.entry_number, side: "call", stop_time: e.call_stop_time });
    }
    if (e.put_side_stopped && e.put_stop_time) {
      out.push({ entry_number: e.entry_number, side: "put", stop_time: e.put_stop_time });
    }
  }
  return out;
}

type StopEventLike = { entry_number: number; side: string; stop_time: string };

interface SPXChartProps {
  /** When provided, the candle/line bars come from THIS array (the polled
   *  snapshot's OHLC) instead of the WS store — for a non-primary IC view.
   *  Omitted (undefined) → read the WS store, byte-identical to the old behavior. */
  ohlc?: OHLCBar[];
  /** When provided, entry markers + strike lines come from THESE entries (the
   *  polled snapshot's entries) instead of the WS store. Stop markers are then
   *  derived from the entries' *_stop_time fields (no WS stop stream off-primary). */
  entries?: HydraEntry[];
  /** Fallback date for time-only stop timestamps, when in prop mode. */
  date?: string | null;
}

export function SPXChart({ ohlc: ohlcProp, entries: entriesProp, date: dateProp }: SPXChartProps = {}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<AnySeries | null>(null);
  const seriesTypeRef = useRef<SeriesType | null>(null);
  const priceLinesRef = useRef<ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]>[]>([]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markersRef = useRef<any>(null);
  const prevEntriesHashRef = useRef("");
  const prevStopCountRef = useRef(0);
  const prevShowStrikesRef = useRef(false);
  const prevSeriesVersionRef = useRef(0);
  // Bumped whenever the price series is recreated (candle↔line) so the markers/
  // price-lines effect re-attaches them to the new series.
  const [seriesVersion, setSeriesVersion] = useState(0);

  // Hooks are always called (Rules of Hooks); a prop, when present, overrides
  // the corresponding WS-store value. No prop → behave EXACTLY as before.
  const storeOHLC = useHydraStore((s) => s.todayOHLC);
  const storeEntries = useHydraStore((s) => s.hydraState?.entries);
  const storeDate = useHydraStore((s) => s.hydraState?.date);
  const storeStopEvents = useHydraStore((s) => s.stopEvents);
  const showStrikes = useHydraStore((s) => s.showStrikes);
  const toggleStrikes = useHydraStore((s) => s.toggleStrikes);

  const usingProps = entriesProp !== undefined;
  const todayOHLC = ohlcProp ?? storeOHLC;
  const stateDate = usingProps ? (dateProp ?? null) : storeDate;
  const entries = useMemo(
    () => entriesProp ?? storeEntries ?? [],
    [entriesProp, storeEntries],
  );
  const stopEvents = useMemo(
    () => (usingProps ? stopEventsFromEntries(entries) : storeStopEvents),
    [usingProps, entries, storeStopEvents],
  );

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
      // Force the crosshair time label to Eastern (market clock).
      localization: {
        timeFormatter: (t: Time) => fmtChartTimeET(t),
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
        // Force axis tick labels to Eastern (market clock), not browser-local.
        tickMarkFormatter: (time: Time) => fmtChartTimeET(time),
      },
      handleScroll: { vertTouchDrag: false },
    });

    chartRef.current = chart;
    // The price series is created lazily in the data effect below, where its
    // type (candle vs line) is chosen from the data's density.

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
      seriesRef.current = null;
      seriesTypeRef.current = null;
    };
  }, []);

  // Update price data when OHLC changes; pick candle vs line from data density.
  useEffect(() => {
    if (!chartRef.current || todayOHLC.length === 0) return;

    const desired = chooseSeriesType(todayOHLC);

    // (Re)create the series if it doesn't exist or the chosen type changed.
    if (!seriesRef.current || seriesTypeRef.current !== desired) {
      if (seriesRef.current) {
        markersRef.current?.detach();
        markersRef.current = null;
        for (const line of priceLinesRef.current) {
          try { seriesRef.current.removePriceLine(line); } catch { /* gone */ }
        }
        priceLinesRef.current = [];
        chartRef.current.removeSeries(seriesRef.current);
      }
      seriesRef.current = makeSeries(chartRef.current, desired);
      seriesTypeRef.current = desired;
      setSeriesVersion((v) => v + 1); // force markers/lines to re-attach
    }

    if (desired === "line") {
      const data = todayOHLC.map((bar) => ({
        time: parseET(bar.timestamp) as Time,
        value: bar.close,
      }));
      (seriesRef.current as ISeriesApi<"Line">).setData(data);
    } else {
      const data = todayOHLC.map((bar) => ({
        time: parseET(bar.timestamp) as Time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      }));
      (seriesRef.current as ISeriesApi<"Candlestick">).setData(data);
    }
    chartRef.current.timeScale().scrollToRealTime();
  }, [todayOHLC]);

  // Update markers and price lines only when entries/stops actually change
  useEffect(() => {
    if (!seriesRef.current) return;

    const currentHash = entriesHash(entries);
    const currentStopCount = stopEvents.length;

    // Skip if nothing changed (OHLC updates won't trigger marker rebuild) —
    // but always rebuild when the series was recreated (candle↔line), since the
    // new series starts with no markers/price lines attached.
    const strikesChanged = showStrikes !== prevShowStrikesRef.current;
    const seriesChanged = seriesVersion !== prevSeriesVersionRef.current;
    if (!seriesChanged && currentHash === prevEntriesHashRef.current && currentStopCount === prevStopCountRef.current && !strikesChanged) {
      return;
    }
    prevEntriesHashRef.current = currentHash;
    prevStopCountRef.current = currentStopCount;
    prevShowStrikesRef.current = showStrikes;
    prevSeriesVersionRef.current = seriesVersion;

    // Build entry markers (exclude fully-skipped entries where both sides were never placed)
    const markers = entries
      .filter((e) => e.entry_time && !isNaN(new Date(e.entry_time).getTime()) && !(e.call_side_skipped && e.put_side_skipped))
      .map((e) => {
        // Prefer close_reason: a Brandon TP/breach sets *_side_stopped as a
        // generic "closed" marker, so flag-inference alone would paint a
        // profitable take-profit red.
        const reason = (e.close_reason || "").toUpperCase();
        const color =
          reason === "TP"
            ? colors.profit
            : reason === "BREACH"
              ? colors.warning
              : e.call_side_stopped && e.put_side_stopped
                ? colors.loss
                : e.call_side_stopped || e.put_side_stopped
                  ? colors.warning
                  : colors.info;
        return {
          time: parseET(e.entry_time!) as Time,
          position: "aboveBar" as const,
          color,
          shape: "arrowDown" as const,
          text: `E${e.entry_number}`,
        };
      });

    // Build stop markers (stop_time may be time-only "12:03:08" from DB)
    const stopMarkers = stopEvents
      .filter((s) => s.stop_time)
      .map((s) => {
        // A Brandon TP/breach also writes trade_stops rows, so color the close
        // markers by the parent entry's close_reason — a take-profit's markers
        // shouldn't be painted red like a real stop.
        const parent = entries.find((e) => e.entry_number === s.entry_number);
        const reason = (parent?.close_reason || "").toUpperCase();
        const color =
          reason === "TP" ? colors.profit
            : reason === "BREACH" ? colors.warning
              : colors.loss;
        return {
          time: parseET(s.stop_time, stateDate ?? undefined) as Time,
          position: "belowBar" as const,
          color,
          shape: "circle" as const,
          text: `S${s.entry_number}${s.side === "call" ? "C" : "P"}`,
        };
      })
      .filter((m) => (m.time as number) > 0);

    const allMarkers = [...markers, ...stopMarkers].sort(
      (a, b) => (a.time as number) - (b.time as number)
    );

    // Detach previous markers before creating new ones (LWC v5 stacking fix)
    markersRef.current?.detach();
    markersRef.current = allMarkers.length > 0
      ? createSeriesMarkers(seriesRef.current, allMarkers)
      : null;

    // Update price lines
    const series = seriesRef.current;
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
  }, [entries, stopEvents, showStrikes, stateDate, seriesVersion]);

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
