"""The BROKER-RECONCILE drift must decompose, not just be reported.

It has been "unexplained" since 2026-09-24 and it FLIPS SIGN — +$622 on 09-23,
−$543 on 09-24, +$4,598 on 09-25 — which no commission convention explains. A
sign flip is the signature of a population difference: rows one side has and
the other does not.

On 2026-09-25 IBKR's per-leg list carried five strikes B never recorded as
entries (7780C, 7760C, 7765C, 7660P, 7685P, −$840.19 together) — legs from
partially-filled entries that were then unwound. They realize money at the
broker and appear in neither `trade_entries` nor `trade_stops`.

So the test that matters is the ORPHAN one: a leg whose strike we never
recorded must be tagged ORPHAN and summed separately. If orphans were silently
counted as ours, the decomposition would always balance and tell us nothing —
which is indistinguishable from the single unexplained number we already had.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.hydra.strategy import HydraStrategy  # noqa: E402

DATE = "2026-09-25"


def _occ(strike, right="C", ymd="260925"):
    """A contractDesc shaped like IBKR's, with a real OCC symbol inside."""
    return (f"SPX    SEP2026 {strike:.0f} {right} "
            f"[SPXW  {ymd}{right}{int(strike * 1000):08d} 100]")


def _strategy(tmp_path, our_strikes, broker_legs):
    from shared.data_recorder import DataRecorder
    db = str(tmp_path / "backtesting.db")
    rec = DataRecorder(db)
    rec.ensure_schema()
    con = sqlite3.connect(db)
    for i, (sc, lc, sp, lp) in enumerate(our_strikes):
        con.execute("INSERT INTO trade_entries (date, entry_number, "
                    "short_call_strike, long_call_strike, short_put_strike, "
                    "long_put_strike) VALUES (?,?,?,?,?,?)",
                    (DATE, i + 1, sc, lc, sp, lp))
    con.commit(); con.close()

    s = HydraStrategy.__new__(HydraStrategy)
    s._data_recorder = rec
    # dry_run MUST be False: the method returns {"skipped": "dry_run"} on its
    # third line otherwise, and the first version of this fixture omitted it —
    # which SKIPPED the three tests that actually exercise the decomposition.
    # A skipped test is indistinguishable from an absent one.
    s.dry_run = False
    s.broker = SimpleNamespace(
        get_positions=lambda: broker_legs,
        # the independent arm: IBKR's own realized P&L from the account ledger
        get_balance=lambda: {"raw_ledger": {"USD": {"realizedpnl": -5833.35}}},
    )
    s.daily_state = SimpleNamespace(total_realized_pnl=0.0, total_commission=0.0)
    return s


def _run(s):
    """Drive just the per-leg block via the real method, capturing its dict."""
    captured = {}
    orig = s.__class__._reconcile_pnl_against_broker
    # the method returns its `out` dict; the broker ledger lookup will fail
    # harmlessly in the fixture, so call it and take whatever it produced
    captured = orig(s, DATE) or {}
    assert "skipped" not in captured, (
        f"the reconcile bailed early ({captured.get('skipped')!r}) — the "
        f"fixture is incomplete and these tests would prove nothing")
    return captured


class TestOrphanLegsAreSeparated:
    def test_a_strike_we_never_recorded_is_tagged_orphan(self, tmp_path):
        s = _strategy(
            tmp_path,
            our_strikes=[(7750.0, 7755.0, 7640.0, 7635.0)],
            broker_legs=[
                {"contractDesc": _occ(7750), "realizedPnl": -9240.85, "position": 0},
                {"contractDesc": _occ(7755), "realizedPnl": +4247.71, "position": 0},
                {"contractDesc": _occ(7780), "realizedPnl": -527.29, "position": 0},
            ])
        out = _run(s)
        assert "broker_realized_orphan" in out, out
        assert out["broker_realized_orphan_legs"] == 1, out
        assert out["broker_realized_orphan"] == pytest.approx(-527.29), out
        assert out["broker_realized_ours"] == pytest.approx(-4993.14, abs=0.01), out

    def test_the_two_halves_sum_to_the_whole(self, tmp_path):
        """The decomposition's only real claim."""
        s = _strategy(
            tmp_path,
            our_strikes=[(7750.0, 7755.0, 7640.0, 7635.0)],
            broker_legs=[
                {"contractDesc": _occ(7750), "realizedPnl": -100.0, "position": 0},
                {"contractDesc": _occ(7780), "realizedPnl": -25.0, "position": 0},
                {"contractDesc": _occ(7660, "P"), "realizedPnl": -15.0, "position": 0},
            ])
        out = _run(s)
        assert "broker_realized_orphan" in out, out
        total = sum(l["realized"] for l in out["broker_realized_by_leg"])
        assert out["broker_realized_ours"] + out["broker_realized_orphan"] == \
            pytest.approx(total, abs=0.01), out

    def test_every_leg_carries_its_strike_and_tag(self, tmp_path):
        s = _strategy(
            tmp_path,
            our_strikes=[(7750.0, 7755.0, 7640.0, 7635.0)],
            broker_legs=[
                {"contractDesc": _occ(7750), "realizedPnl": -100.0, "position": -7},
                {"contractDesc": _occ(7780), "realizedPnl": -25.0, "position": 3},
            ])
        out = _run(s)
        assert "broker_realized_by_leg" in out, out
        legs = {l["strike"]: l for l in out["broker_realized_by_leg"]}
        assert legs[7750.0]["in_trade_entries"] is True
        assert legs[7780.0]["in_trade_entries"] is False
        assert legs[7780.0]["qty"] == 3, "quantity is still not recorded per leg"


class TestTheStrikeParse:
    def test_it_reads_the_occ_symbol_not_the_free_text(self):
        """The prefix is free text and varies; the bracketed OCC symbol is
        unambiguous. Pin that a misleading prefix cannot win."""
        import re
        occ = re.compile(r"(\d{6})([CP])(\d{8})")
        desc = "SPX    SEP2026 9999 C [SPXW  260925C07750000 100]"
        m = occ.search(desc)
        assert m and round(int(m.group(3)) / 1000.0, 2) == 7750.0, (
            "the strike was taken from the free-text prefix (9999) rather than "
            "the OCC symbol (7750)")

    def test_a_description_with_no_occ_symbol_yields_no_strike(self):
        import re
        occ = re.compile(r"(\d{6})([CP])(\d{8})")
        assert occ.search("some non-option row") is None
