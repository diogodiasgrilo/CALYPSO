/** Financial performance statistics computed from daily P&L values. */

/** Annualized Sharpe Ratio: mean/std * sqrt(252). */
export function sharpeRatio(dailyPnls: number[]): number {
  if (dailyPnls.length < 2) return 0;
  const mean = dailyPnls.reduce((a, b) => a + b, 0) / dailyPnls.length;
  const variance =
    dailyPnls.reduce((sum, v) => sum + (v - mean) ** 2, 0) /
    (dailyPnls.length - 1);
  const std = Math.sqrt(variance);
  if (std === 0) return 0;
  return (mean / std) * Math.sqrt(252);
}

/** Annualized Sortino Ratio: mean/downside_std * sqrt(252). */
export function sortinoRatio(dailyPnls: number[]): number {
  if (dailyPnls.length < 2) return 0;
  const mean = dailyPnls.reduce((a, b) => a + b, 0) / dailyPnls.length;
  const downsideValues = dailyPnls.filter((v) => v < 0);
  if (downsideValues.length === 0) return mean > 0 ? Infinity : 0;
  const downsideVariance =
    downsideValues.reduce((sum, v) => sum + v ** 2, 0) / (dailyPnls.length - 1);
  const downsideStd = Math.sqrt(downsideVariance);
  if (downsideStd === 0) return 0;
  return (mean / downsideStd) * Math.sqrt(252);
}

/** Max drawdown from cumulative P&L curve. */
export function maxDrawdown(dailyPnls: number[]): {
  value: number;
  peak: number;
  trough: number;
} {
  if (dailyPnls.length === 0) return { value: 0, peak: 0, trough: 0 };
  let cumulative = 0;
  let peak = 0;
  let maxDd = 0;
  let ddPeak = 0;
  let ddTrough = 0;

  for (const pnl of dailyPnls) {
    cumulative += pnl;
    if (cumulative > peak) peak = cumulative;
    const dd = peak - cumulative;
    if (dd > maxDd) {
      maxDd = dd;
      ddPeak = peak;
      ddTrough = cumulative;
    }
  }
  return { value: maxDd, peak: ddPeak, trough: ddTrough };
}

/** Calmar Ratio: annualized return / max drawdown. */
export function calmarRatio(dailyPnls: number[]): number {
  if (dailyPnls.length === 0) return 0;
  const totalReturn = dailyPnls.reduce((a, b) => a + b, 0);
  const annualized = (totalReturn / dailyPnls.length) * 252;
  const dd = maxDrawdown(dailyPnls).value;
  if (dd === 0) return annualized > 0 ? Infinity : 0;
  return annualized / dd;
}

/** Profit Factor: sum(wins) / abs(sum(losses)). */
export function profitFactor(dailyPnls: number[]): number {
  const wins = dailyPnls.filter((v) => v > 0).reduce((a, b) => a + b, 0);
  const losses = Math.abs(
    dailyPnls.filter((v) => v < 0).reduce((a, b) => a + b, 0)
  );
  if (losses === 0) return wins > 0 ? Infinity : 0;
  return wins / losses;
}

/** Expectancy: avg_win * win% - avg_loss * loss%. */
export function expectancy(dailyPnls: number[]): number {
  if (dailyPnls.length === 0) return 0;
  const wins = dailyPnls.filter((v) => v > 0);
  const losses = dailyPnls.filter((v) => v < 0);
  const winPct = wins.length / dailyPnls.length;
  const lossPct = losses.length / dailyPnls.length;
  const avgWin = wins.length > 0 ? wins.reduce((a, b) => a + b, 0) / wins.length : 0;
  const avgLoss =
    losses.length > 0
      ? Math.abs(losses.reduce((a, b) => a + b, 0) / losses.length)
      : 0;
  return avgWin * winPct - avgLoss * lossPct;
}

/** Average Win / Average Loss ratio. */
export function avgWinLossRatio(dailyPnls: number[]): number {
  const wins = dailyPnls.filter((v) => v > 0);
  const losses = dailyPnls.filter((v) => v < 0);
  const avgWin =
    wins.length > 0 ? wins.reduce((a, b) => a + b, 0) / wins.length : 0;
  const avgLoss =
    losses.length > 0
      ? Math.abs(losses.reduce((a, b) => a + b, 0) / losses.length)
      : 0;
  if (avgLoss === 0) return avgWin > 0 ? Infinity : 0;
  return avgWin / avgLoss;
}
