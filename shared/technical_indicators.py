"""
technical_indicators.py - Technical Analysis Indicators

Implements technical indicators for trading strategies:
- EMA (Exponential Moving Average)
- MACD (Moving Average Convergence Divergence)
- CCI (Commodity Channel Index)

Used by the Rolling Put Diagonal (RPD) strategy for entry/exit filters.

Author: Trading Bot Developer
Date: 2026-01-19
"""

import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TechnicalIndicatorValues:
    """
    Container for technical indicator values.

    Used for entry/exit filtering based on Bill Belt's criteria.
    """
    # Current price
    current_price: float = 0.0

    # EMA
    ema_9: float = 0.0
    price_above_ema: bool = False
    ema_distance_pct: float = 0.0  # % distance from EMA

    # MACD
    macd_line: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    macd_histogram_rising: bool = False
    macd_histogram_positive: bool = False

    # CCI
    cci: float = 0.0
    cci_overbought: bool = False   # Above 100
    cci_oversold: bool = False     # Below -100

    # Weekly trend (optional)
    weekly_ema_9: float = 0.0
    weekly_trend_bearish: bool = False

    # STRATEGY-001: Candle analysis for Bill Belt entry rule
    # "At least 2 daily green candles closed above MA9 line"
    consecutive_green_candles_above_ema: int = 0
    last_candle_is_green: bool = False
    last_candle_closed_above_ema: bool = False

    @property
    def entry_conditions_met(self) -> bool:
        """
        Check if all entry conditions are satisfied per Bill Belt's rules.

        Entry requires:
        1. Price > 9 EMA (bullish trend)
        2. MACD histogram rising OR positive
        3. CCI not overbought (< 100) - NOTE: This is optional, not in Bill's original rules
        4. Weekly trend not bearish
        """
        return (
            self.price_above_ema and
            (self.macd_histogram_rising or self.macd_histogram_positive) and
            not self.cci_overbought and
            not self.weekly_trend_bearish
        )

    @property
    def bill_belt_entry_met(self) -> bool:
        """
        STRATEGY-001: Check Bill Belt's specific entry criteria.

        Per Bill Belt: "At least 2 daily green candles that are closed and
        above the MA9 line and the MACD lines are bullish."

        Returns:
            True if Bill Belt's specific entry criteria are met
        """
        return (
            self.consecutive_green_candles_above_ema >= 2 and
            (self.macd_histogram_rising or self.macd_histogram_positive) and
            not self.weekly_trend_bearish
        )

    @property
    def exit_signal(self) -> bool:
        """
        STRATEGY-002: Check if price has broken below EMA (exit signal).

        Bill Belt: "If the price drops under the MA9, either close the spread
        or buy back the short put and let the long put appreciate as the price drops."

        This is triggered when price goes below EMA - NOT waiting for 2-3% drop.
        """
        return not self.price_above_ema

    def to_dict(self) -> dict:
        """Convert to dictionary for logging."""
        return {
            "current_price": self.current_price,
            "ema_9": self.ema_9,
            "price_above_ema": self.price_above_ema,
            "ema_distance_pct": self.ema_distance_pct,
            "macd_line": self.macd_line,
            "macd_signal": self.macd_signal,
            "macd_histogram": self.macd_histogram,
            "macd_histogram_rising": self.macd_histogram_rising,
            "macd_histogram_positive": self.macd_histogram_positive,
            "cci": self.cci,
            "cci_overbought": self.cci_overbought,
            "cci_oversold": self.cci_oversold,
            "weekly_ema_9": self.weekly_ema_9,
            "weekly_trend_bearish": self.weekly_trend_bearish,
            "consecutive_green_candles_above_ema": self.consecutive_green_candles_above_ema,
            "last_candle_is_green": self.last_candle_is_green,
            "last_candle_closed_above_ema": self.last_candle_closed_above_ema,
            "entry_conditions_met": self.entry_conditions_met,
            "bill_belt_entry_met": self.bill_belt_entry_met,
            "exit_signal": self.exit_signal
        }


def calculate_sma(prices: List[float], period: int) -> float:
    """
    Calculate Simple Moving Average for the last 'period' prices.

    Args:
        prices: List of prices (oldest to newest)
        period: Number of periods for SMA

    Returns:
        SMA value, or 0.0 if insufficient data
    """
    if len(prices) < period:
        return 0.0
    return sum(prices[-period:]) / period


def calculate_ema(prices: List[float], period: int) -> List[float]:
    """
    Calculate Exponential Moving Average.

    EMA formula: EMA_today = (Price_today * k) + (EMA_yesterday * (1 - k))
    where k = 2 / (period + 1)

    Args:
        prices: List of closing prices (oldest to newest)
        period: EMA period (e.g., 9 for 9 EMA)

    Returns:
        List of EMA values (same length as prices, NaN for initial values)
    """
    if len(prices) < period:
        logger.debug(f"Insufficient data for EMA: {len(prices)} prices, need {period}")
        return [float('nan')] * len(prices)

    ema_values = []
    multiplier = 2 / (period + 1)

    # First EMA is SMA of first 'period' prices
    sma = sum(prices[:period]) / period
    ema_values.extend([float('nan')] * (period - 1))
    ema_values.append(sma)

    # Calculate subsequent EMAs
    for i in range(period, len(prices)):
        ema = (prices[i] * multiplier) + (ema_values[-1] * (1 - multiplier))
        ema_values.append(ema)

    return ema_values


def get_current_ema(prices: List[float], period: int) -> float:
    """
    Get the current (latest) EMA value.

    Args:
        prices: List of closing prices (oldest to newest)
        period: EMA period

    Returns:
        Current EMA value, or 0.0 if insufficient data
    """
    ema_values = calculate_ema(prices, period)
    if not ema_values or ema_values[-1] != ema_values[-1]:  # Check for NaN
        return 0.0
    return ema_values[-1]


def calculate_macd(
    prices: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9
) -> Tuple[List[float], List[float], List[float]]:
    """
    Calculate MACD (Moving Average Convergence Divergence).

    MACD Line = Fast EMA (12) - Slow EMA (26)
    Signal Line = 9 EMA of MACD Line
    Histogram = MACD Line - Signal Line

    Args:
        prices: List of closing prices
        fast_period: Fast EMA period (default 12)
        slow_period: Slow EMA period (default 26)
        signal_period: Signal line period (default 9)

    Returns:
        Tuple of (macd_line, signal_line, histogram) lists
    """
    if len(prices) < slow_period + signal_period:
        nan_list = [float('nan')] * len(prices)
        return nan_list, nan_list, nan_list

    fast_ema = calculate_ema(prices, fast_period)
    slow_ema = calculate_ema(prices, slow_period)

    # MACD line = Fast EMA - Slow EMA
    macd_line = []
    for f, s in zip(fast_ema, slow_ema):
        if f == f and s == s:  # Not NaN
            macd_line.append(f - s)
        else:
            macd_line.append(float('nan'))

    # Signal line = 9 EMA of MACD line (only valid MACD values)
    valid_macd = [m for m in macd_line if m == m]  # Filter out NaN
    if len(valid_macd) < signal_period:
        nan_list = [float('nan')] * len(prices)
        return macd_line, nan_list, nan_list

    signal_ema = calculate_ema(valid_macd, signal_period)

    # Pad signal line to match MACD length
    nan_count = len(macd_line) - len(valid_macd)
    signal_line = [float('nan')] * nan_count + signal_ema

    # Histogram = MACD - Signal
    histogram = []
    for m, s in zip(macd_line, signal_line):
        if m == m and s == s:  # Not NaN
            histogram.append(m - s)
        else:
            histogram.append(float('nan'))

    return macd_line, signal_line, histogram


def get_current_macd(
    prices: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9
) -> Tuple[float, float, float]:
    """
    Get the current (latest) MACD values.

    Args:
        prices: List of closing prices
        fast_period: Fast EMA period (default 12)
        slow_period: Slow EMA period (default 26)
        signal_period: Signal line period (default 9)

    Returns:
        Tuple of (macd_line, signal_line, histogram)
        Returns (0.0, 0.0, 0.0) if insufficient data
    """
    macd_line, signal_line, histogram = calculate_macd(
        prices, fast_period, slow_period, signal_period
    )

    def safe_last(lst: List[float]) -> float:
        if not lst:
            return 0.0
        val = lst[-1]
        return val if val == val else 0.0  # Return 0 if NaN

    return safe_last(macd_line), safe_last(signal_line), safe_last(histogram)


def calculate_cci(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 20
) -> List[float]:
    """
    Calculate Commodity Channel Index (CCI).

    CCI = (Typical Price - SMA of TP) / (0.015 * Mean Deviation)
    Typical Price = (High + Low + Close) / 3

    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of closing prices
        period: CCI period (default 20)

    Returns:
        List of CCI values
    """
    if len(closes) < period or len(highs) < period or len(lows) < period:
        logger.debug(f"Insufficient data for CCI: {len(closes)} bars, need {period}")
        return [float('nan')] * len(closes)

    # Calculate Typical Price
    typical_prices = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]

    cci_values = [float('nan')] * (period - 1)

    for i in range(period - 1, len(typical_prices)):
        # SMA of Typical Price
        tp_window = typical_prices[i - period + 1:i + 1]
        sma_tp = sum(tp_window) / period

        # Mean Deviation = average of |TP - SMA|
        mean_dev = sum(abs(tp - sma_tp) for tp in tp_window) / period

        # CCI calculation (constant 0.015 ensures ~70-80% of values between -100 and +100)
        if mean_dev != 0:
            cci = (typical_prices[i] - sma_tp) / (0.015 * mean_dev)
        else:
            cci = 0.0

        cci_values.append(cci)

    return cci_values


def get_current_cci(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 20
) -> float:
    """
    Get the current (latest) CCI value.

    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of closing prices
        period: CCI period (default 20)

    Returns:
        Current CCI value, or 0.0 if insufficient data
    """
    cci_values = calculate_cci(highs, lows, closes, period)
    if not cci_values:
        return 0.0
    val = cci_values[-1]
    return val if val == val else 0.0  # Return 0 if NaN


def is_macd_histogram_rising(histogram: List[float], lookback: int = 2) -> bool:
    """
    Check if MACD histogram is rising.

    Bill Belt: "MACD Histogram must be rising or positive."

    Args:
        histogram: List of histogram values
        lookback: Number of periods to check (default 2)

    Returns:
        True if histogram is rising over lookback period
    """
    valid = [h for h in histogram if h == h]  # Filter NaN
    if len(valid) < lookback + 1:
        return False

    recent = valid[-(lookback + 1):]
    return recent[-1] > recent[0]


def count_consecutive_green_candles_above_ema(
    opens: List[float],
    closes: List[float],
    ema_values: List[float]
) -> Tuple[int, bool, bool]:
    """
    STRATEGY-001: Count consecutive green candles that closed above EMA.

    Bill Belt's entry rule: "At least 2 daily green candles that are
    closed and above the MA9 line."

    A green candle = close > open (bullish candle)
    Above EMA = close > EMA value for that bar

    Args:
        opens: List of open prices (oldest to newest)
        closes: List of closing prices (oldest to newest)
        ema_values: List of EMA values (same length as prices)

    Returns:
        Tuple of:
        - consecutive_count: Number of consecutive green candles above EMA (from most recent)
        - last_is_green: Whether the most recent candle is green
        - last_above_ema: Whether the most recent candle closed above EMA
    """
    if not opens or not closes or not ema_values:
        return 0, False, False

    if len(opens) != len(closes) or len(opens) != len(ema_values):
        logger.warning(f"Mismatched array lengths: opens={len(opens)}, closes={len(closes)}, ema={len(ema_values)}")
        return 0, False, False

    # Check the most recent candle first
    last_close = closes[-1]
    last_open = opens[-1]
    last_ema = ema_values[-1]

    # Handle NaN EMA
    if last_ema != last_ema:  # NaN check
        return 0, False, False

    last_is_green = last_close > last_open
    last_above_ema = last_close > last_ema

    # Count consecutive green candles above EMA from most recent backwards
    consecutive_count = 0
    for i in range(len(closes) - 1, -1, -1):
        close = closes[i]
        open_price = opens[i]
        ema = ema_values[i]

        # Skip NaN EMAs
        if ema != ema:
            break

        is_green = close > open_price
        is_above_ema = close > ema

        if is_green and is_above_ema:
            consecutive_count += 1
        else:
            break

    return consecutive_count, last_is_green, last_above_ema


def calculate_all_indicators(
    prices: List[float],
    highs: Optional[List[float]] = None,
    lows: Optional[List[float]] = None,
    opens: Optional[List[float]] = None,
    ema_period: int = 9,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    cci_period: int = 20,
    cci_overbought: float = 100.0,
    cci_oversold: float = -100.0
) -> TechnicalIndicatorValues:
    """
    Calculate all technical indicators and return a complete values object.

    Args:
        prices: List of closing prices (oldest to newest)
        highs: List of high prices (optional, uses closes if not provided)
        lows: List of low prices (optional, uses closes if not provided)
        opens: List of open prices (optional, needed for candle analysis)
        ema_period: Period for EMA (default 9)
        macd_fast: MACD fast period (default 12)
        macd_slow: MACD slow period (default 26)
        macd_signal: MACD signal period (default 9)
        cci_period: CCI period (default 20)
        cci_overbought: CCI overbought threshold (default 100)
        cci_oversold: CCI oversold threshold (default -100)

    Returns:
        TechnicalIndicatorValues with all calculated indicators
    """
    if not prices:
        logger.warning("No prices provided for indicator calculation")
        return TechnicalIndicatorValues()

    # Use closes for highs/lows if not provided
    if highs is None:
        highs = prices
    if lows is None:
        lows = prices

    current_price = prices[-1]

    # Calculate EMA (full list for candle analysis)
    ema_values = calculate_ema(prices, ema_period)
    ema_9 = ema_values[-1] if ema_values and ema_values[-1] == ema_values[-1] else 0.0
    price_above_ema = current_price > ema_9 if ema_9 > 0 else False
    ema_distance_pct = ((current_price - ema_9) / ema_9 * 100) if ema_9 > 0 else 0.0

    # Calculate MACD
    macd_line, macd_signal_val, macd_hist = get_current_macd(
        prices, macd_fast, macd_slow, macd_signal
    )
    _, _, histogram_list = calculate_macd(prices, macd_fast, macd_slow, macd_signal)
    macd_histogram_rising = is_macd_histogram_rising(histogram_list, lookback=2)
    macd_histogram_positive = macd_hist > 0

    # Calculate CCI
    cci = get_current_cci(highs, lows, prices, cci_period)
    cci_is_overbought = cci > cci_overbought
    cci_is_oversold = cci < cci_oversold

    # STRATEGY-001: Calculate consecutive green candles above EMA
    consecutive_green = 0
    last_is_green = False
    last_above_ema = False

    if opens is not None and len(opens) == len(prices):
        consecutive_green, last_is_green, last_above_ema = count_consecutive_green_candles_above_ema(
            opens, prices, ema_values
        )
    else:
        # If no opens provided, we can't do candle analysis
        # Default to checking if current price is above EMA
        last_above_ema = price_above_ema

    return TechnicalIndicatorValues(
        current_price=current_price,
        ema_9=ema_9,
        price_above_ema=price_above_ema,
        ema_distance_pct=ema_distance_pct,
        macd_line=macd_line,
        macd_signal=macd_signal_val,
        macd_histogram=macd_hist,
        macd_histogram_rising=macd_histogram_rising,
        macd_histogram_positive=macd_histogram_positive,
        cci=cci,
        cci_overbought=cci_is_overbought,
        cci_oversold=cci_is_oversold,
        weekly_ema_9=0.0,  # Set separately if needed
        weekly_trend_bearish=False,  # Set separately if needed
        consecutive_green_candles_above_ema=consecutive_green,
        last_candle_is_green=last_is_green,
        last_candle_closed_above_ema=last_above_ema
    )


# Test function
if __name__ == "__main__":
    print("=" * 70)
    print("TECHNICAL INDICATORS TEST")
    print("=" * 70)

    # Sample price data (simulating 50 days of QQQ)
    sample_prices = [
        480.0, 482.5, 481.0, 483.2, 485.0, 484.5, 486.0, 488.2, 487.0, 489.5,
        490.0, 491.5, 490.8, 492.0, 493.5, 494.0, 493.2, 495.0, 496.5, 497.0,
        498.2, 497.5, 499.0, 500.5, 501.0, 500.2, 502.0, 503.5, 504.0, 505.5,
        504.8, 506.0, 507.5, 508.0, 509.2, 510.0, 509.5, 511.0, 512.5, 513.0,
        514.2, 513.5, 515.0, 516.5, 517.0, 518.2, 519.0, 520.5, 521.0, 522.0
    ]

    print(f"\nTest data: {len(sample_prices)} price bars")
    print(f"Latest price: ${sample_prices[-1]:.2f}")

    # Calculate individual indicators
    print("\n--- Individual Indicators ---")

    ema_9 = get_current_ema(sample_prices, 9)
    print(f"9 EMA: ${ema_9:.2f}")

    macd_line, signal_line, histogram = get_current_macd(sample_prices)
    print(f"MACD Line: {macd_line:.4f}")
    print(f"Signal Line: {signal_line:.4f}")
    print(f"Histogram: {histogram:.4f}")

    # For CCI, we need high/low data (using closes as approximation)
    cci = get_current_cci(sample_prices, sample_prices, sample_prices)
    print(f"CCI: {cci:.2f}")

    # Calculate all indicators
    print("\n--- All Indicators ---")
    indicators = calculate_all_indicators(sample_prices)

    print(f"Current Price: ${indicators.current_price:.2f}")
    print(f"9 EMA: ${indicators.ema_9:.2f}")
    print(f"Price above EMA: {indicators.price_above_ema}")
    print(f"EMA Distance: {indicators.ema_distance_pct:.2f}%")
    print(f"MACD Histogram: {indicators.macd_histogram:.4f}")
    print(f"MACD Rising: {indicators.macd_histogram_rising}")
    print(f"MACD Positive: {indicators.macd_histogram_positive}")
    print(f"CCI: {indicators.cci:.2f}")
    print(f"CCI Overbought: {indicators.cci_overbought}")
    print(f"CCI Oversold: {indicators.cci_oversold}")

    print("\n--- Entry Conditions ---")
    print(f"Entry conditions met: {indicators.entry_conditions_met}")
    print(f"Exit signal (price << EMA): {indicators.exit_signal}")

    print("\n" + "=" * 70)
