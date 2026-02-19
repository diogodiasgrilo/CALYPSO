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
- One side non-viable in NEUTRAL market: Convert to one-sided entry on viable side
- One side non-viable in trending market: Skip if preferred side is non-viable
- Both non-viable: Skip entry entirely

Based on Tammy Chambless's MEIC strategy with trend-following concepts from METF.

Version History:
- 1.3.0 (2026-02-19): MKT-018 early close based on ROC (close all positions when ROC >= 2%)
- 1.2.9 (2026-02-18): MKT-017 daily loss limit, Fix #77/#78/#79 (settlement, summary accuracy, counters)
- 1.2.8 (2026-02-17): EMA threshold 0.2%, MKT-016 stop cascade breaker
- 1.2.7 (2026-02-16): Daily Summary column redesign, Fix #76 fill price field names
- 1.2.6 (2026-02-13): Fix #75 - Async deferred stop fill lookup (non-blocking P&L correction)
- 1.2.5 (2026-02-13): Fix #74 - Stop loss fill price accuracy (deferred lookup was bypassed by quote fallback)
- 1.2.4 (2026-02-13): Code audit hardening - error handling, timeout protection, documentation
- 1.2.3 (2026-02-12): Fix #70 - Accurate fill price tracking (verify vs PositionBase.OpenPrice)
- 1.2.2 (2026-02-12): Fix #65-#68 - Recovery classification, long overlap, timeout protection
- 1.2.1 (2026-02-12): Fix #71-#73 - Duplicate summary prevention, net P&L, active entries fix
- 1.2.0 (2026-02-12): Accurate P&L tracking and daily summary fixes
- 1.1.8 (2026-02-11): Fix #64 - Google Sheets API timeout protection (prevents bot freeze)
- 1.1.7 (2026-02-11): Fix #63 - EUR conversion in Trades tab (pass saxo_client to log_trade)
- 1.1.6 (2026-02-11): Fix #62 - EMA values now logged to Account Summary tab
- 1.1.5 (2026-02-11): MKT-014 liquidity re-check, counter tracking, position merge detection
- 1.1.4 (2026-02-10): MKT-013 same-strike overlap prevention
- 1.1.3 (2026-02-10): Logging accuracy (Fix #49), correct MKT-011/MKT-010/trend labels
- 1.1.2 (2026-02-10): P&L tracking fixes (Fix #46/#47), expired vs skipped distinction
- 1.1.1 (2026-02-09): Hybrid credit gate - respects trend filter in non-NEUTRAL markets
- 1.1.0 (2026-02-08): MKT-011 credit gate, MKT-010 illiquidity fallback
- 1.0.0 (2026-02-04): Initial implementation with EMA trend detection
"""

from bots.meic_tf.strategy import MEICTFStrategy, TrendSignal, TFIronCondorEntry

__all__ = [
    "MEICTFStrategy",
    "TrendSignal",
    "TFIronCondorEntry",
]
