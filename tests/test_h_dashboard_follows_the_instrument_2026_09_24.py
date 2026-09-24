"""H's page must plot the instrument H actually trades.

**A regression introduced by the SPX→SPY switch, caught the same session.**
``_market_db()`` resolved to the LIVE SEAT — variant B, which trades SPX. The
moment H moved to SPY the page would have drawn **SPX at ~7,700 against SPY
strikes at ~765**: the strikes would sit far off the axis, and the "did it break
the band?" verdict computed from that path would describe a different market.
Nothing would have errored. The chart would simply have been about the S&P
index while the position was in an ETF.

That is the same failure shape as the rest of this session's findings — a number
that is confidently wrong rather than visibly broken — and it is the reason the
resolution now matches on the SYMBOL instead of on a seat.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dashboard.backend.routers.long_strangle as ls_router  # noqa: E402


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A miniature data/ tree: H on SPY, a live SPX seat, and a SPY sibling."""
    data = tmp_path / "data"
    cfgs = tmp_path / "cfg"
    cfgs.mkdir()
    layout = {"h": "SPY", "b": "SPX", "e": "SPY"}
    for vid, sym in layout.items():
        (data / f"variant_{vid}").mkdir(parents=True)
        (data / f"variant_{vid}" / "backtesting.db").write_text("")
        (cfgs / f"{vid}.json").write_text(
            json.dumps({"strategy": {"underlying_symbol": sym}}))

    class S:
        pass
    s = S()
    for vid in layout:
        setattr(s, f"variant_{vid}_state_file",
                str(data / f"variant_{vid}" / "hydra_state.json"))
        setattr(s, f"variant_{vid}_config_file", str(cfgs / f"{vid}.json"))
    monkeypatch.setattr(ls_router, "settings", s)
    return data, cfgs


class TestTheMarketDbFollowsTheSymbol:

    def test_it_prefers_H_s_own_database(self, fleet):
        """Its ticks are by definition the instrument H traded that day, which
        also keeps history self-consistent: 2026-09-23's rows are SPX and pair
        correctly with that day's SPX strikes."""
        data, _ = fleet
        assert ls_router._market_db() == str(
            data / "variant_h" / "backtesting.db")

    def test_it_NEVER_falls_back_to_the_live_SPX_seat(self, fleet):
        """The regression, stated directly. With H's own DB absent the old code
        reached for the live seat; B trades SPX and must never be chosen for a
        SPY strategy."""
        data, _ = fleet
        (data / "variant_h" / "backtesting.db").unlink()
        chosen = ls_router._market_db()
        assert chosen is not None, "a SPY sibling exists and should be used"
        assert "variant_b" not in chosen
        assert chosen == str(data / "variant_e" / "backtesting.db")

    def test_the_siblings_list_holds_only_matching_underlyings(self, fleet):
        sibs = ls_router._sibling_market_dbs()
        assert any("variant_e" in p for p in sibs)
        assert not any("variant_b" in p for p in sibs)
        assert not any("variant_h" in p for p in sibs)

    def test_no_match_yields_nothing_rather_than_something_wrong(self, fleet):
        """An empty path renders no band. That is honest; a band drawn from
        another instrument is not."""
        data, cfgs = fleet
        (data / "variant_h" / "backtesting.db").unlink()
        (cfgs / "e.json").write_text(
            json.dumps({"strategy": {"underlying_symbol": "QQQ"}}))
        assert ls_router._market_db() is None
        assert ls_router._sibling_market_dbs() == []

    def test_the_symbol_is_read_from_each_variants_own_config(self, fleet):
        assert ls_router._underlying_of("h") == "SPY"
        assert ls_router._underlying_of("b") == "SPX"

    def test_an_unreadable_config_degrades_instead_of_raising(self, fleet):
        _, cfgs = fleet
        (cfgs / "h.json").write_text("{not json")
        assert ls_router._underlying_of("h") is None
        ls_router._market_db()          # must not raise

    def test_matching_ignores_case_and_whitespace(self, fleet):
        _, cfgs = fleet
        (cfgs / "e.json").write_text(
            json.dumps({"strategy": {"underlying_symbol": " spy "}}))
        assert any("variant_e" in p for p in ls_router._sibling_market_dbs())


class TestThePageIsToldWhichSymbolItIsShowing:

    def test_the_frontend_no_longer_hardcodes_SPX_in_the_tooltip(self):
        src = (ROOT / "dashboard" / "frontend" / "src" / "pages"
               / "LongStrangle.tsx").read_text()
        assert '"SPX"]}' not in src, (
            "the chart tooltip must name the symbol actually plotted")
        assert "symbol: string;" in src
        assert "status.underlying_symbol" in src

    def test_the_status_route_publishes_the_symbol(self):
        src = (ROOT / "dashboard" / "backend" / "routers"
               / "long_strangle.py").read_text()
        assert '"underlying_symbol"' in src
