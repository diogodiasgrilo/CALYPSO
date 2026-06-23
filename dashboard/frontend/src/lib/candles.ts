/**
 * Candle continuity for 1-min SPX OHLC bars.
 *
 * The recorder aggregates `market_ticks` into 1-min OHLC. When the densest
 * recorder (variant A) goes idle late in the session — e.g. after it flattens
 * its positions ~15:50 — its heartbeat slows to ~1 tick/minute, so those
 * minutes aggregate to a SINGLE price: open = high = low = close. A zero-body,
 * zero-wick candle renders in Lightweight Charts as a floating horizontal dash
 * ("cross"), which looks broken (2026-06-22 report).
 *
 * `withCandleContinuity` gives such a bar a real body using the standard OHLC
 * convention `open[n] = close[n-1]` (and spans high/low to include it), so the
 * sparse tail draws as connected candles tracking the price instead of
 * disconnected dashes. Non-flat (real) bars pass through unchanged; all extra
 * fields (timestamp, vix, …) are preserved. Pure + side-effect-free.
 */
export interface OHLCish {
  open: number;
  high: number;
  low: number;
  close: number;
}

export function withCandleContinuity<T extends OHLCish>(bars: T[]): T[] {
  let prevClose: number | null = null;
  return bars.map((bar) => {
    const flat =
      bar.open === bar.high && bar.high === bar.low && bar.low === bar.close;
    // A flat bar with a known previous close gets that close as its open so it
    // has a body; the first bar (no prevClose) is left as-is.
    const open = flat && prevClose !== null ? prevClose : bar.open;
    const out: T = {
      ...bar,
      open,
      high: Math.max(bar.high, open, bar.close),
      low: Math.min(bar.low, open, bar.close),
    };
    prevClose = bar.close;
    return out;
  });
}
