"""
MEIC-TF (Trend Following Hybrid) Trading Bot

A modified MEIC bot that adds EMA-based trend direction detection and
pre-entry credit validation.

Before each entry, checks 20 EMA vs 40 EMA on SPX 1-minute bars:
- BULLISH: Place PUT spread only (calls are risky in uptrend)
- BEARISH: Place CALL spread only (puts are risky in downtrend)
- NEUTRAL: Place full iron condor (standard MEIC behavior)

Credit Gate (MKT-011): Before placing orders, estimates credit from quotes.
- Both sides viable: Proceed with trend signal
- One side non-viable: Force one-sided entry on viable side
- Both non-viable: Skip entry entirely

Based on Tammy Chambless's MEIC strategy with trend-following concepts from METF.

Version History:
- 1.1.0 (2026-02-08): MKT-011 credit gate, MKT-010 illiquidity fallback
- 1.0.0 (2026-02-04): Initial implementation with EMA trend detection
"""

from bots.meic_tf.strategy import MEICTFStrategy, TrendSignal, TFIronCondorEntry

__all__ = [
    "MEICTFStrategy",
    "TrendSignal",
    "TFIronCondorEntry",
]
