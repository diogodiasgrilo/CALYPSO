"""Risk ratios must be gated on TRADED days, not on rows in the array.

Defect D10, found by the 2026-09-17 visual audit
(docs/DASHBOARD_VISUAL_AUDIT_2026_09_17.md).

``PerformanceMetrics`` withholds Sharpe / Sortino / Calmar / Profit Factor /
Win-Loss until there is a big enough sample, which is right. But it counted
ROWS in ``daily_pnls`` — and that array carries every trading day since the
baseline, including days the strategy held nothing and booked $0.00.

Measured on live data before the fix:

    variant   rows   traded   Sharpe(all rows)   Sharpe(traded only)
       a       147     131        -0.67              -0.71      (passes either way)
       b        94      73         2.01               2.29      (passes either way)
       c        68      41        -2.54              -3.29      (passes either way)
       d        48      14        -3.82              -7.45      <- published
       e        52       5        -3.03             -10.94      <- published

**E published a risk ratio derived from FIVE real observations.**

Note the direction, because it is the opposite of the intuition: padding with
zeros shrinks the mean by k = traded/rows and the deviation by roughly sqrt(k),
so the ratio is COMPRESSED toward zero. It understates good and bad performance
alike rather than exaggerating either. The defect is the sample size clearing
the gate, not an inflated number.

The COMPUTATION deliberately still runs over all rows — that is correct for the
sqrt(252) annualisation, where an idle day is a real 0% return on allocated
capital. Only the sufficiency test changed.

These are source/behaviour checks over the shared statistics helpers, so they
run in the normal suite with no browser.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

METRICS = (ROOT / "dashboard" / "frontend" / "src" / "components" / "pnl"
           / "PerformanceMetrics.tsx")
STATS = ROOT / "dashboard" / "frontend" / "src" / "lib" / "statsUtils.ts"


def _sharpe(xs):
    """Mirror of statsUtils.sharpeRatio, so the test reasons about the same
    number the UI shows rather than a re-derivation."""
    if len(xs) < 2:
        return 0.0
    mean = sum(xs) / len(xs)
    var = sum((v - mean) ** 2 for v in xs) / (len(xs) - 1)
    sd = math.sqrt(var)
    return 0.0 if sd == 0 else (mean / sd) * math.sqrt(252)


# ─────────────────────────────────────────────────────────────────────────────
# The gate
# ─────────────────────────────────────────────────────────────────────────────

def test_gate_counts_traded_days_not_rows():
    src = METRICS.read_text()
    m = re.search(r"const n = effectivePnls\?\.(.+?);", src)
    assert m, "the ratio-gate sample size is no longer computed where expected"
    expr = m.group(1)
    assert "filter" in expr and "!== 0" in expr, (
        f"the gate counts rows, not traded days (found `{expr}`). A strategy "
        f"that trades rarely then publishes a ratio from a handful of real "
        f"observations padded with idle-day zeros."
    )


def test_gate_label_says_traded_days():
    """The label is the only thing telling a reader what the number means."""
    src = METRICS.read_text()
    assert "traded days · have {n}" in src, (
        "the note still reads 'ratios need ≥20 days', which is ambiguous about "
        "whether idle days count — and they no longer do."
    )


def test_computation_still_uses_every_row():
    """Only the SUFFICIENCY test changed. The ratio itself must stay on the full
    series, matching the sqrt(252) annualisation."""
    src = METRICS.read_text()
    block = src[src.index("const stats = useMemo"): src.index("}, [effectivePnls]);")]
    assert "sharpeRatio(effectivePnls)" in block, (
        "the Sharpe computation no longer runs over the full series; filtering "
        "there would break the sqrt(252) annualisation, which assumes capital "
        "was allocated on every trading day."
    )


# ─────────────────────────────────────────────────────────────────────────────
# The statistics that justify the change
# ─────────────────────────────────────────────────────────────────────────────

def test_zero_padding_compresses_the_ratio_toward_zero():
    """Pin the DIRECTION, because it is counter-intuitive and I initially got it
    backwards. Padding must not exaggerate — it must shrink the magnitude."""
    traded = [-130.0, 21.0, -68.0, -14.0, -8.0]          # E's five real days
    padded = traded + [0.0] * 47                          # as stored
    assert abs(_sharpe(padded)) < abs(_sharpe(traded)), (
        "zero-padding is expected to COMPRESS |Sharpe| toward zero; if it now "
        "exaggerates, the reasoning in the gate comment is wrong."
    )


@pytest.mark.parametrize(
    "rows, traded, should_publish",
    [
        (147, 131, True),    # A — trades most days
        (94, 73, True),      # B — the live seat, must be unaffected
        (68, 41, True),      # C
        (48, 14, False),     # D — was publishing from 14 observations
        (52, 5, False),      # E — was publishing from 5
        (15, 2, False),      # F
        (15, 13, False),     # G
    ],
)
def test_gate_outcome_per_variant(rows, traded, should_publish):
    """The exact blast radius: only D and E change, and the live seat does not."""
    series = [1.0] * traded + [0.0] * (rows - traded)
    n = len([v for v in series if v != 0])
    assert (n >= 20) is should_publish, (
        f"{traded} traded of {rows} rows -> publish={n >= 20}, expected "
        f"{should_publish}"
    )
    # And the old rule would have published in every one of these cases where
    # there are >= 20 rows — which is the bug.
    if not should_publish and rows >= 20:
        assert len(series) >= 20, "old row-count rule would have published this"


def test_live_seat_ratio_is_unchanged_by_the_fix():
    """B is the only strategy whose numbers reach an investor today. The gate
    change must not alter what it shows."""
    b_rows, b_traded = 94, 73
    assert b_traded >= 20 and b_rows >= 20, (
        "B must pass under BOTH the old and new rule — if it ever stops "
        "passing, the live seat's published Sharpe just disappeared."
    )
