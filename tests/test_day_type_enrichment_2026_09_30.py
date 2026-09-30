"""day_type and realized_volatility were NULL on all 48 live-era days.

NOT the INSERT-OR-IGNORE gap the recorder's docstring describes — HOMER's
writer was already upgraded to `ON CONFLICT(date) DO UPDATE`. The real cause is
that HOMER derives day_type from a Google Sheets **"Notes"** column, and Sheets
was retired 2026-07-17. It has been reading a source that no longer exists.

Why it matters: "does slot e#4 lose on TREND days or CHOP days?" was
unanswerable in every analysis this week, because there was nothing to
condition on. A 0DTE condor's losing days are trend days, and pooling them with
chop days is why per-slot results carried so much variance.

THESE TESTS EXECUTE the classifier. Source-grep tests would not have caught
either bug I wrote building it: a call to `is_early_close_day(date)` when the
function takes a datetime, and a reference to a bare `opex` name that does not
exist — the second would have raised NameError inside settlement, for every
variant.
"""
from __future__ import annotations

import os
import pytest
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _s(o, h, l, c, fomc=False):
    s = HydraStrategy.__new__(HydraStrategy)
    s.market_data = SimpleNamespace(spx_open=o, spx_high=h, spx_low=l)
    s._resolve_spx_close = lambda: c
    s.fomc_announcement_today = fomc
    s._data_recorder = None
    return s


class TestItClassifiesTheDay:
    def test_a_clean_trend_day_is_trend(self):
        """Opens at the low, closes at the high — |c-o|/range = 1.0."""
        assert _s(7700, 7780, 7700, 7778)._classify_day_type([]) == "trend"

    def test_a_round_trip_is_chop(self):
        """Same range, but it came back — |c-o|/range ~ 0."""
        assert _s(7700, 7780, 7620, 7705)._classify_day_type([]) == "chop"

    def test_2026_09_21_the_worst_live_day_reads_trend(self):
        """The real session: SPX 7692.02 open, 7779.04 high, settled 7765.
        Every stop that day was call-side. If this reads 'chop' the column is
        useless for the thing it exists to separate."""
        assert _s(7692.02, 7779.04, 7692.02, 7765.0)._classify_day_type([]) == "trend"

    def test_2026_09_29_reads_chop(self):
        """Yesterday: 7710.54 open, 7723.75 high, 7666.73 low, 7684.80 close."""
        assert _s(7710.54, 7723.75, 7666.73, 7684.80)._classify_day_type([]) == "chop"

    def test_fomc_wins_over_the_range(self):
        """An FOMC day is an FOMC day even if it also trended — the label is
        for conditioning, and event days are their own population."""
        assert _s(7700, 7780, 7700, 7778, fomc=True)._classify_day_type([]) == "fomc"

    def test_an_fomc_event_string_is_also_detected(self):
        assert _s(7700, 7710, 7690, 7700)._classify_day_type(["FOMC Statement"]) == "fomc"


class TestItCannotBreakSettlement:
    def test_a_zero_range_does_not_divide_by_zero(self):
        assert _s(7700, 7700, 7700, 7700)._classify_day_type([]) == "normal"

    def test_opex_is_not_a_day_type(self):
        """Opex has its own column and is orthogonal to how the day traded.

        `opex_week` is populated on all 103 live rows. An opex Friday is still
        either a trend day or a chop day, so folding it in here would erase the
        one distinction this column exists to draw. The first draft had an
        unreachable `opex` branch below trend/chop plus a parameter that caused
        a NameError in settlement; this pins that neither came back.
        """
        import inspect
        sig = inspect.signature(HydraStrategy._classify_day_type)
        assert list(sig.parameters) == ["self", "events"], (
            f"signature drifted: {sig} — an extra parameter here is what "
            f"produced the settlement NameError")
        # A clean opex-week trend day must still read "trend".
        assert _s(7700, 7780, 7700, 7778)._classify_day_type([]) == "trend"

    def test_missing_market_data_returns_normal(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.market_data = None
        s._resolve_spx_close = lambda: 0
        s.fomc_announcement_today = False
        assert s._classify_day_type([]) in {"normal", "early_close"}

    def test_a_raising_close_resolver_is_swallowed(self):
        """It runs inside settlement. Enrichment must never block the write."""
        s = _s(7700, 7780, 7700, 7778)
        def boom():
            raise RuntimeError("no close")
        s._resolve_spx_close = boom
        assert s._classify_day_type([]) == "normal"


class TestRealizedVolatilityIsActuallyComputable:
    def test_the_recorder_method_it_depends_on_EXISTS(self):
        """The first version called get_spx_price_series_for_date, which did
        not exist — so the helper would have returned None forever and the
        column would have looked collected while staying empty. That is the
        exact failure this whole change is fixing."""
        from shared.data_recorder import DataRecorder
        assert hasattr(DataRecorder, "get_spx_price_series_for_date")

    def test_it_returns_none_rather_than_zero_on_a_short_path(self):
        """NULL reads as 'not measured'; 0.0 reads as 'measured, and flat'."""
        s = _s(7700, 7710, 7690, 7700)
        s._data_recorder = SimpleNamespace(
            get_spx_price_series_for_date=lambda d: [7700.0, 7701.0])
        assert s._realized_volatility() is None

    def test_it_computes_a_positive_number_on_a_real_path(self):
        import random
        random.seed(3)
        px, p = [], 7700.0
        for _ in range(400):
            p *= (1 + random.gauss(0, 0.0003))
            px.append(p)
        s = _s(7700, max(px), min(px), px[-1])
        s._data_recorder = SimpleNamespace(get_spx_price_series_for_date=lambda d: px)
        v = s._realized_volatility()
        assert v is not None and v > 0, v


class TestNoUndefinedLocals:
    """The NameError guard — static, because the dynamic one was blind.

    I shipped `self._classify_day_type(events, opex)` where no `opex` name
    exists. Valid syntax, passing tests, and a NameError inside settlement for
    every variant the first time it ran.

    My first attempt at a guard built a mock strategy and called
    `_record_daily_summary_to_db`. It passed with the bug REINTRODUCED: the
    method hit an unrelated AttributeError on the incomplete double first, and
    the test's own `except Exception` swallowed it. A test that cannot fail is
    not a test.

    This asks the question directly instead of trying to reach the line: for
    every function in the module, is every name it READS either assigned
    locally, a parameter, an import, a closure variable, a module global, or a
    builtin? `opex` is none of those. Generalises to any undefined local in any
    method, not just the one I happened to write.
    """

    MODULES = [
        "bots/hydra/strategy.py",
        "bots/hydra/base_strategy.py",
        "bots/hydra/brandon/strategy.py",
        "shared/data_recorder.py",
    ]

    # Module dunders exist at runtime but symtable does not list them.
    _DUNDERS = {"__file__", "__name__", "__doc__", "__spec__",
                "__package__", "__loader__", "__builtins__", "__class__"}

    @staticmethod
    def _scan(path):
        import symtable, builtins, io as _io, os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        full = os.path.join(root, path)
        src = _io.open(full, encoding="utf-8").read()
        top = symtable.symtable(src, path, "exec")
        module_names = {sym.get_name() for sym in top.get_symbols()}
        known = module_names | set(dir(builtins)) | TestNoUndefinedLocals._DUNDERS
        bad = []

        def walk(tbl, trail):
            for child in tbl.get_children():
                walk(child, trail + [child.get_name()])
            if tbl.get_type() != "function":
                return
            for sym in tbl.get_symbols():
                name = sym.get_name()
                if (sym.is_assigned() or sym.is_parameter() or sym.is_imported()
                        or sym.is_local() or sym.is_free()):
                    continue
                if not sym.is_referenced() or name in known:
                    continue
                bad.append((".".join(trail), name, tbl.get_lineno()))

        walk(top, [])
        return bad

    @pytest.mark.parametrize("module", MODULES)
    def test_every_name_read_resolves(self, module):
        bad = self._scan(module)
        assert not bad, (
            f"{module} reads names that are bound nowhere — each is a NameError "
            f"waiting for the branch to execute: "
            + "; ".join(f"{scope} -> {name!r} (line {ln})" for scope, name, ln in bad))

    def test_the_check_can_actually_fail(self):
        """Negative control.

        Without this, a checker that silently scanned nothing would pass all
        four cases above and look like proof.
        """
        import symtable, builtins
        src = "def f():\n    return undefined_thing\n"
        top = symtable.symtable(src, "<ctl>", "exec")
        fn = top.get_children()[0]
        known = ({s.get_name() for s in top.get_symbols()}
                 | set(dir(builtins)) | self._DUNDERS)
        flagged = [s.get_name() for s in fn.get_symbols()
                   if s.is_referenced() and not (
                       s.is_assigned() or s.is_parameter() or s.is_imported()
                       or s.is_local() or s.is_free())
                   and s.get_name() not in known]
        assert flagged == ["undefined_thing"], (
            f"the scope check does not detect a plainly undefined name "
            f"(got {flagged!r}) — every passing result above is meaningless")
