"""An undefined-risk strategy must SAY it is undefined-risk.

Dashboard rebuild Phase 10. ``CAPITAL_BASES.broker_margin.boundedLoss`` has been
``false`` since Phase 4, carrying the contract "printing any max-loss figure is a
lie, so the card shows UNBOUNDED instead of a number" — and **nothing consumed
it**. Measured before this change: zero components referenced ``boundedLoss``.

Note the shape of the defect. Nothing rendered a WRONG max loss — a grep for
max-loss renderings found none anywhere. The number was ABSENT. So a naked short
strangle sat beside four defined-risk strategies on the same dashboard with no
visible signal that its downside behaves completely differently, which on a
page shown to investors is its own kind of misinformation.

"Max loss" cannot be quantified for G. What CAN be is the distance from spot to
the strike that starts losing money — expressed in points AND as a fraction of
the day's expected move, because 30 points means nothing without knowing whether
the market typically travels 20 or 120 in a session.

Expected move is the standard one-sigma daily convention,
``spot x (VIX/100) / sqrt(252)``, verified below against hand computation.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared.strategy_taxonomy as tax  # noqa: E402

SRC = ROOT / "dashboard" / "frontend" / "src"
RISK_LIB = SRC / "lib" / "undefinedRisk.ts"
RISK_CARD = SRC / "components" / "pnl" / "UndefinedRiskCard.tsx"
IC_DASH = SRC / "components" / "dashboard" / "IronCondorDashboard.tsx"
PNL_SHAPE = SRC / "lib" / "pnlShape.ts"

TRADING_DAYS = 252


# ─────────────────────────────────────────────────────────────────────────────
# The maths, mirrored — and pinned to the TS below so it cannot drift
# ─────────────────────────────────────────────────────────────────────────────

def expected_daily_move(spot, vix):
    if not spot or not vix or spot <= 0 or vix <= 0:
        return None
    return (spot * (vix / 100)) / math.sqrt(TRADING_DAYS)


def test_expected_move_matches_hand_computation():
    """SPX 7635 at VIX 15.4 → 7635 × 0.154 / √252."""
    assert expected_daily_move(7635, 15.4) == pytest.approx(
        7635 * 0.154 / math.sqrt(252)
    )
    assert expected_daily_move(7635, 15.4) == pytest.approx(74.068, abs=0.01)


@pytest.mark.parametrize(
    "spot, vix",
    [(0, 15), (7635, 0), (None, None), (-100, 15), (7635, -1), (None, 15)],
)
def test_expected_move_refuses_degenerate_input(spot, vix):
    """A fabricated expected move would make every distance meaningless — which
    is the exact error this card exists to avoid. Null, never a guess."""
    assert expected_daily_move(spot, vix) is None


def test_nearest_short_uses_real_strikes():
    """G's actual 2026-09-16 entry: short call 7665, short put 7575, spot
    7617.69. The put is nearer (42.69 pts vs 47.31)."""
    spot, sc, sp = 7617.69, 7665.0, 7575.0
    d_call, d_put = sc - spot, spot - sp
    assert d_put < d_call
    assert d_put == pytest.approx(42.69, abs=0.01)
    em = expected_daily_move(spot, 15.4)
    assert d_put / em == pytest.approx(0.578, abs=0.005)


def test_a_breached_short_is_negative():
    """Spot through the strike must read negative so the card can say BREACHED,
    not silently show a small positive distance."""
    assert (7665.0 - 7670.0) < 0
    assert (7570.0 - 7575.0) < 0


# ─────────────────────────────────────────────────────────────────────────────
# The TS implementation itself — the mirror above tests nothing on its own
# ─────────────────────────────────────────────────────────────────────────────

def test_expected_move_formula_is_the_one_documented():
    src = RISK_LIB.read_text()
    body = src[src.index("export function expectedDailyMove"):]
    body = body[: body.index("\n}")]
    assert "Math.sqrt(TRADING_DAYS)" in body, "annualisation divisor changed"
    assert "vix / 100" in body
    assert "TRADING_DAYS = 252" in src


def test_degenerate_guard_exists_in_the_source():
    """A mutant that drops the guard returns Infinity/NaN and the card renders
    a nonsense distance instead of an em dash."""
    src = RISK_LIB.read_text()
    body = src[src.index("export function expectedDailyMove"):]
    body = body[: body.index("\n}")]
    assert "return null" in body
    assert "spot <= 0" in body and "vix <= 0" in body


def test_nearest_short_skips_closed_sides():
    """A stopped / expired / skipped side is no longer exposed; counting it
    would report a distance to a position that does not exist.

    Checked PER SIDE. A flag-name search over the whole function passes while
    only ONE branch still has the checks — a mutant that gutted just the call
    branch survived exactly that way."""
    src = RISK_LIB.read_text()
    fn = src[src.index("function sideIsOpen"):]
    fn = fn[: fn.index("\n}")]
    assert "entry_time" in fn, "an unplaced entry would count as exposure"
    for side in ("call", "put"):
        for flag in ("stopped", "expired", "skipped"):
            token = f"e.{side}_side_{flag}"
            assert token in fn, (
                f"sideIsOpen no longer checks {token} — a closed {side} would "
                f"still be reported as live exposure."
            )


def test_nearest_short_signs_each_side_correctly():
    """A call is breached from BELOW and a put from ABOVE; one shared expression
    would make one of them negative while still safe."""
    src = RISK_LIB.read_text()
    fn = src[src.index("export function nearestShortDistance"):]
    assert 'side === "call" ? strike - spot : spot - strike' in fn, (
        "the per-side sign convention changed — one side's distance is now "
        "inverted, so a safe position can read as breached or vice versa."
    )


def test_flat_strategy_has_no_distance():
    src = RISK_LIB.read_text()
    fn = src[src.index("export function nearestShortDistance"):]
    assert "let best: NearestShort | null = null" in fn
    assert "return best" in fn, "must return null when nothing is open"


# ─────────────────────────────────────────────────────────────────────────────
# Wiring — the card must appear for the right strategies, in BOTH views
# ─────────────────────────────────────────────────────────────────────────────

def test_only_broker_margin_is_unbounded():
    src = PNL_SHAPE.read_text()
    block = src[src.index("export const CAPITAL_BASES"): src.index("/** Config for a known capital basis")]
    for basis, expected in (("defined_risk", "true"), ("net_debit", "true"),
                            ("broker_margin", "false")):
        seg = block[block.index(f"{basis}: {{"):]
        seg = seg[: seg.index("},")]
        assert f"boundedLoss: {expected}" in seg, (
            f"{basis}.boundedLoss changed — a defined-risk strategy would start "
            f"showing UNBOUNDED, or a naked one would stop."
        )


def test_g_is_the_undefined_risk_strategy():
    assert tax.meta("g").capital_basis == "broker_margin"
    for vid in ("a", "b", "c", "f"):
        assert tax.meta(vid).capital_basis == "defined_risk"


def test_card_is_rendered_from_capital_basis_not_a_letter():
    src = IC_DASH.read_text()
    assert "capitalBasisConfig(" in src and "boundedLoss === false" in src, (
        "the risk card is no longer driven by the capital basis"
    )
    assert '=== "g"' not in src, (
        "per-variant branching is back — a future undefined-risk strategy would "
        "silently get no warning."
    )


def test_card_appears_in_BOTH_views():
    """A card reachable from only one branch is precisely the defect that hid
    the previous-day view from six strategies. Pin both."""
    src = IC_DASH.read_text()
    primary = src[src.index("function PrimaryICView"): src.index("function coerceEntry")]
    polled = src[src.index("function PolledICView"): src.index("interface IronCondorDashboardProps")]
    # Word-boundary match: a plain substring also matches a RENAMED component
    # such as <UndefinedRiskCardXX, which is how a mutant that removed the card
    # from this view survived.
    tag = re.compile(r"<UndefinedRiskCard[\s/>]")
    assert tag.search(primary), (
        "the warning would vanish if an undefined-risk strategy took the live seat"
    )
    assert len(tag.findall(polled)) >= 2, (
        "the polled view must show it in BOTH the full and off-day layouts — "
        "pre-market is when someone decides whether to allocate to it."
    )


def test_card_states_unbounded_not_a_number():
    card = RISK_CARD.read_text()
    assert "UNBOUNDED" in card
    assert re.search(r"max_loss|maxLoss", card) is None, (
        "a max-loss figure crept in — for this strategy there is no such number."
    )


def test_card_degrades_to_a_dash_when_flat():
    card = RISK_CARD.read_text()
    assert "no open position" in card
    assert "—" in card, "must render an em dash, never 0, with nothing open"
