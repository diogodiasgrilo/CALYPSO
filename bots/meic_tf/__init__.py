"""
MEIC-TF (Trend Following Hybrid) Trading Bot

A modified MEIC bot that adds EMA-based trend direction detection.
Before each entry, checks 20 EMA vs 40 EMA on SPX 1-minute bars:
- BULLISH: Place PUT spread only (calls are risky in uptrend)
- BEARISH: Place CALL spread only (puts are risky in downtrend)
- NEUTRAL: Place full iron condor (standard MEIC behavior)

Based on Tammy Chambless's MEIC strategy with trend-following concepts from METF.
"""

from bots.meic_tf.strategy import MEICTFStrategy, TrendSignal, TFIronCondorEntry

__all__ = [
    "MEICTFStrategy",
    "TrendSignal",
    "TFIronCondorEntry",
]
