"""A day's headline can be dominated by P&L that was never a trade.

`daily_summaries.unattributed_overlay_pnl` is the round-trip price P&L of a
failed entry attempt's unwound legs (ORDER-010). It is real money and correctly
inside `net_pnl` — but it is not a trading result.

2026-09-24 on the live seat:

    headline net_pnl        +$3,331.55
    unattributed             +$5,995.00
    what it actually traded  -$2,663.45

The History heat map painted that as the month's second-best day, on a day the
strategy lost money. Five of the seven sessions to 09-28 were distorted; that
one flips the SIGN.

The column has been served all along — `get_daily_summaries` does `SELECT *` —
and nothing read it. The fix is presentational and deliberately conservative:
the headline stays, because it is what the account made, and the trading number
is shown beside it.
"""
from __future__ import annotations

from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "dashboard" / "frontend" / "src"
_TYPES = _SRC / "components" / "history" / "types.ts"
_DETAIL = _SRC / "components" / "history" / "DayDetailSummary.tsx"
_CAL = _SRC / "components" / "history" / "MonthCalendar.tsx"


class TestTheFieldSurvivesToTheFrontend:
    def test_the_api_already_sends_it(self):
        """`SELECT *` — no backend change was needed, and a future narrowing
        of that query would silently empty this whole feature."""
        src = (Path(__file__).resolve().parents[1] / "dashboard" / "backend"
               / "services" / "db_reader.py").read_text()
        # BOTH readers, each in its own window. The first version took one
        # 600-char slice from the first `def`, which spanned both methods —
        # so narrowing either query on its own still passed.
        for fn in ("async def get_daily_summaries(",
                   "async def get_daily_summaries_by_year("):
            assert fn in src, f"{fn} missing"
            i = src.index(fn)
            j = src.index("async def", i + 10)
            assert "SELECT *" in src[i:j], (
                f"{fn.strip()} no longer selects *, so "
                "unattributed_overlay_pnl may not reach the page")

    def test_the_type_carries_it(self):
        assert "unattributed_overlay_pnl" in _TYPES.read_text()


class TestTheDayDetailShowsTheSplit:
    def test_it_computes_the_trading_number(self):
        src = _DETAIL.read_text()
        assert "const tradingPnl" in src
        assert "unattributed_overlay_pnl" in src

    def test_it_renders_both_sides(self):
        """Showing only the headline is the bug; showing only the trading
        number would be a different one — the account really did make the
        headline."""
        src = _DETAIL.read_text()
        assert "{formatPnL(tradingPnl)}" in src, "trading number not rendered"
        assert "{formatPnL(unattributed)}" in src, "the residue is not rendered"
        assert "summary.net_pnl" in src, "the headline was removed"

    def test_it_stays_quiet_when_there_is_nothing_to_split(self):
        """Most days have no residue. A banner on every one of them is noise."""
        src = _DETAIL.read_text()
        assert "const distorted" in src and "{distorted && (" in src


class TestTheHeatMapMarksDistortedDays:
    def test_the_cell_computes_the_predicate(self):
        src = _CAL.read_text()
        assert "const misleading" in src
        assert "Math.sign(traded) !== Math.sign(pnl)" in src, (
            "a sign flip is the case that matters most and is not tested for")

    def test_the_mark_is_actually_rendered(self):
        """Computing it and not drawing it is the blind-grep failure this repo
        has hit before — pin the render, not the identifier."""
        src = _CAL.read_text()
        assert "{misleading && (" in src, "the marker is not gated on the predicate"
        assert "rounded-full" in src, "no marker element is drawn"

    def test_the_marker_has_a_legend(self):
        """An unexplained dot is worse than no dot.

        Scoped to the legend BLOCK. The first version asserted the phrase
        appeared anywhere in the file and passed with the legend gutted,
        because the tooltip carries the same words.
        """
        src = _CAL.read_text()
        assert "{anyMisleading && (" in src, "no legend block"
        i = src.index("{anyMisleading && (")
        legend = src[i:i + 600]
        assert "failed-entry unwind" in legend, (
            "the legend does not say what the dot means")
        assert "rounded-full" in legend, (
            "the legend does not show the marker it is explaining")

    def test_the_legend_only_appears_when_a_day_is_marked(self):
        src = _CAL.read_text()
        assert "{anyMisleading && (" in src

    def test_the_tooltip_states_both_numbers(self):
        """The heat map is scanned, not read — the hover has to carry the
        correction or the dot just sends you hunting.

        Anchored on the copy rather than on surrounding indentation: the first
        version matched the whitespace of the JSX and broke on formatting.
        """
        src = _CAL.read_text()
        assert "headline — but" in src, "no corrected tooltip branch"
        i = src.index("headline — but")
        window = src[i - 80:i + 260]
        assert "traded" in window, "the tooltip does not give the trading number"
        assert "failed-entry unwind" in window, (
            "the tooltip does not say what the difference is")

    def test_the_headline_still_drives_the_colour(self):
        """Deliberate: the cell colour is the account's P&L. Recolouring by the
        trading number would make the calendar disagree with the balance."""
        src = _CAL.read_text()
        assert "const intensity = Math.min(Math.abs(pnl) / monthMax, 1)" in src
