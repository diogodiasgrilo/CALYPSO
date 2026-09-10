"""
IBKR percent-suffixed field parsing (2026-09-10).

THE BUG
-------
`IBClient._parse_quote_row`'s inner `f()` did a bare `float(v)` inside a
try/except that swallowed ValueError. IBKR returns implied volatility (field
7633) as a percent-suffixed STRING — `'11.8%'` — so `float('11.8%')` raised and
`iv` silently became None, every single time. No percent-stripping existed
anywhere in the repo.

WHAT IT COST
------------
We concluded IBKR does not return IV for SPXW. That conclusion is recorded in
`bots/hydra/__init__.py`'s 2026-06-15 VM-probe note and became **NO-GO reason
2** in `docs/migration/D_GOLIVE_SCOPE_AND_AUDIT.md` — "the edge signal is
unobservable, D trades its own edge blind", used to argue against building
strategy D. A 2026-09-09 probe of D's own open position returned all four legs
with live IV and a full term structure (call front-back +1.6 vol pts, put
+2.1). One missing `.rstrip('%')` cost roughly a year of a wrong premise.

SCOPE GUARD
-----------
The 'C'/'H' price-field prefixes IBKR also emits are deliberately NOT handled
here — that would change after-hours/settlement price parsing on the live seat.
A test below pins that they still return None, so the omission is a recorded
decision rather than something a future reader mistakes for coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.ib_client import IBClient  # noqa: E402
from shared.ib_constants import (  # noqa: E402
    FIELD_ASK, FIELD_BID, FIELD_DELTA, FIELD_IV, FIELD_LAST,
)

parse = IBClient._parse_quote_row


class TestIVPercentParsing:
    def test_the_real_ibkr_shape_parses_to_a_fraction(self):
        """The exact value the 2026-09-09 probe saw on D's short call."""
        out = parse({FIELD_IV: "11.8%"}, conid=904013343, include_greeks=True)
        assert out["iv"] == pytest.approx(0.118)

    def test_all_four_legs_from_the_real_probe(self):
        for raw, expected in (("11.8%", 0.118), ("10.2%", 0.102),
                              ("14.5%", 0.145), ("12.4%", 0.124)):
            assert parse({FIELD_IV: raw}, include_greeks=True)["iv"] == pytest.approx(expected)

    def test_NEGATIVE_CONTROL_this_used_to_be_None(self):
        """Pins the actual defect: a bare float() on this input raises, and the
        old code turned that into None. If this ever reads None again, the
        percent handling has been removed."""
        with pytest.raises(ValueError):
            float("11.8%")
        assert parse({FIELD_IV: "11.8%"}, include_greeks=True)["iv"] is not None

    def test_a_plain_numeric_string_is_unchanged(self):
        assert parse({FIELD_IV: "0.118"}, include_greeks=True)["iv"] == pytest.approx(0.118)

    def test_a_float_is_unchanged(self):
        assert parse({FIELD_IV: 0.118}, include_greeks=True)["iv"] == pytest.approx(0.118)

    def test_whitespace_is_tolerated(self):
        assert parse({FIELD_IV: "  11.8%  "}, include_greeks=True)["iv"] == pytest.approx(0.118)

    def test_a_malformed_percent_still_returns_None_not_a_crash(self):
        assert parse({FIELD_IV: "abc%"}, include_greeks=True)["iv"] is None
        assert parse({FIELD_IV: "%"}, include_greeks=True)["iv"] is None

    def test_missing_and_empty_are_still_None(self):
        assert parse({}, include_greeks=True)["iv"] is None
        assert parse({FIELD_IV: ""}, include_greeks=True)["iv"] is None
        assert parse({FIELD_IV: None}, include_greeks=True)["iv"] is None

    def test_zero_percent_is_zero_not_None(self):
        """0.0 and None mean different things downstream — _dc_read_iv treats a
        non-positive IV as 'no signal' but must not confuse it with a read
        failure."""
        assert parse({FIELD_IV: "0%"}, include_greeks=True)["iv"] == 0.0


class TestNoCollateralDamage:
    def test_ordinary_price_fields_are_untouched(self):
        out = parse({FIELD_BID: "1.20", FIELD_ASK: "1.30", FIELD_LAST: "1.25"})
        assert out["bid"] == pytest.approx(1.20)
        assert out["ask"] == pytest.approx(1.30)
        assert out["last"] == pytest.approx(1.25)
        assert out["mid"] == pytest.approx(1.25)

    def test_the_crossed_quote_guard_still_holds(self):
        """P7-audit L9 — mid must stay None on a crossed book."""
        assert parse({FIELD_BID: "1.40", FIELD_ASK: "1.30"})["mid"] is None

    def test_other_greeks_are_untouched(self):
        out = parse({FIELD_DELTA: "-0.35"}, include_greeks=True)
        assert out["delta"] == pytest.approx(-0.35)

    def test_price_prefixes_STILL_return_None_by_design(self):
        """DELIBERATE non-fix. IBKR decorates price fields with 'C' (close) or
        'H' (halted). Handling those would change after-hours/settlement price
        parsing on the LIVE seat — the same class of change behind the
        2026-07-06 stale-SPX phantom. If someone later fixes it on purpose,
        this test should be updated, not silently deleted."""
        assert parse({FIELD_LAST: "C7638.04"})["last"] is None
        assert parse({FIELD_LAST: "H7638.04"})["last"] is None
