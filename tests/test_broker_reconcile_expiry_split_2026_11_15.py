"""IBKR's realized P&L excludes EXPIRED legs — the drift was never one number.

THE FINDING (2026-11-15). `BROKER-RECONCILE` has logged an "unexplained" drift
between IBKR's ledger `realizedpnl` and our gross at every settlement since
2026-09-24, and it FLIPS SIGN, which was repeatedly read as evidence of a
defect. It is not. IBKR's per-position `realizedPnl` contains ONLY positions
closed or offset by an opposing TRADE; a leg that runs to cash settlement never
appears in it at all. Our gross books every leg. So the two quantities are
structurally different and

    ours_gross - IBKR = (our gross on legs that EXPIRED)
                      + (IBKR commission on the legs that TRADED)

The first term carries either sign — hence the sign flips — and the second is
always positive.

THE EVIDENCE, both halves measured on the live seat `bl`:

  * CATEGORICAL, 2026-11-13: of our 16 (right, strike) positions, 9 were
    predicted present in IBKR's realized list and 7 predicted absent. All 16
    were correct, with no false positive in either direction. That includes the
    `7595 C` whose absence had been logged as unexplained for two days — it is
    E#5's long call, never offset, so IBKR never realized it.

  * ARITHMETIC, 2026-11-12 (no orphan, no over-fill): expired legs -$245.00
    plus implied commission +$121.68 = -$123.32 against a logged drift of
    -$123.31. TO THE CENT.

Two months of chasing a structural difference as an anomaly is the lesson. The
orphan hypothesis the drift had been pinned on was ONE contributor of three
(orphans, expiries, commission), and a number that is merely unexplained
invites whichever story is told first.

WHAT IS ACTUALLY GUARDED HERE. The dollar decomposition is informational. The
alarm is CATEGORICAL: IBKR realizing a position we booked as EXPIRED means we
booked a traded close as a worthless expiry — the same family as the 2026-10-14
settlement defect (OTM expiries booked ITM off a day-stale SPX, -$12,710 of
phantom record) — and it is the only check of that family whose evidence comes
from the broker instead of from our own price feed.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.strategy import HydraStrategy  # noqa: E402

C = 7  # contracts per entry on both sessions

# ---------------------------------------------------------------- real data --
# (entry, SC, LC, SP, LP, scFill, lcFill, spFill, lpFill)
NOV12_ENTRIES = [
    (1, 7680.0, 7685.0, 7580.0, 7575.0, 0.55, 0.50, 1.50, 1.20),
    (2, 7670.0, 7675.0, 7585.0, 7580.0, 1.20, 0.90, 0.80, 0.70),
    (3, 7665.0, 7670.0, 7585.0, 7580.0, 0.50, 0.35, 1.05, 1.00),
]
NOV12_CLOSES = [
    (1, "short_put", "BUY", 7, 1.000000),
    (1, "long_put", "SELL", 7, 0.264286),
    (2, "short_put", "BUY", 7, 1.585714),
    (2, "long_put", "SELL", 7, 0.564286),
    (3, "short_put", "BUY", 7, 1.500000),
    (3, "long_put", "SELL", 7, 0.607143),
]
# IBKR's own per-position realized, read at settlement 2026-11-12.
NOV12_IBKR = [
    ("SPX    NOV2026 7585 P [SPXW  261112P07585000 100]", -900.22),
    ("SPX    NOV2026 7575 P [SPXW  261112P07575000 100]", -672.29),
    ("SPX    NOV2026 7670 C [SPXW  261112C07670000 100]", 577.71),
    ("SPX    NOV2026 7580 P [SPXW  261112P07580000 100]", -71.88),
]
NOV12_OURS_GROSS = -1189.9993      # daily_summaries.gross_pnl
NOV12_LEDGER = -1066.69            # raw_ledger.USD.realizedpnl

NOV13_ENTRIES = [
    (1, 7570.0, 7575.0, 7445.0, 7440.0, 2.75, 1.80, 0.90, 0.814286),
    (2, 7585.0, 7590.0, 7475.0, 7470.0, 1.45, 1.15, 1.05, 1.00),
    (3, 7580.0, 7585.0, 7480.0, 7475.0, 0.75, 0.70, 1.00, 0.90),
    (4, 7580.0, 7585.0, 7500.0, 7495.0, 2.45, 1.65, 0.5571428571428572, 0.50),
    (5, 7590.0, 7595.0, 7515.0, 7510.0, 1.00, 0.6285714285714287, 0.75, 0.65),
    (6, 7585.0, 7590.0, 7520.0, 7515.0, 0.30, 0.20, 0.95, 0.80),
]
NOV13_CLOSES = [
    (1, "short_call", "BUY", 7, 0.200000),
    (1, "long_call", "SELL", 7, 0.100000),
    (1, "short_put", "BUY", 7, 0.050000),
    (4, "short_call", "BUY", 7, 0.100000),
    (4, "long_call", "SELL", 7, 0.050000),
    (4, "short_put", "BUY", 7, 0.100000),
    (3, "short_call", "BUY", 7, 0.292857),
    (3, "long_call", "SELL", 7, 0.100000),
    (2, "short_call", "BUY", 7, 0.300000),
    (2, "long_call", "SELL", 7, 0.150000),
    (6, "short_call", "BUY", 7, 0.400000),
    (6, "long_call", "SELL", 7, 0.100000),
]
NOV13_IBKR = [
    ("SPX    NOV2026 7580 C [SPXW  261113C07580000 100]", 1773.12),
    ("SPX    NOV2026 7570 C [SPXW  261113C07570000 100]", 1767.71),
    ("SPX    NOV2026 7575 C [SPXW  261113C07575000 100]", -1207.29),
    ("SPX    NOV2026 7585 C [SPXW  261113C07585000 100]", -959.79),
    ("SPX    NOV2026 7445 P [SPXW  261113P07445000 100]", 579.39),
    ("SPX    NOV2026 7500 P [SPXW  261113P07500000 100]", 302.99),
    ("SPX    NOV2026 7590 C [SPXW  261113C07590000 100]", -174.59),
    ("SPX    NOV2026 7490 P [SPXW  261113P07490000 100]", -122.01),  # real orphan
    ("SPX    NOV2026 7475 P [SPXW  261113P07475000 100]", 87.71),
    ("SPX    NOV2026 7515 P [SPXW  261113P07515000 100]", -51.66),
]

# What the mechanism predicts, written out so the assertions are not circular.
NOV12_TRADED = {("P", 7580.0), ("P", 7575.0), ("P", 7585.0), ("C", 7670.0)}
NOV12_EXPIRED = {("C", 7680.0), ("C", 7685.0), ("C", 7675.0), ("C", 7665.0)}
NOV13_TRADED = {("C", 7570.0), ("C", 7575.0), ("C", 7580.0), ("C", 7585.0),
                ("C", 7590.0), ("P", 7445.0), ("P", 7475.0), ("P", 7500.0),
                ("P", 7515.0)}
NOV13_EXPIRED = {("C", 7595.0), ("P", 7440.0), ("P", 7470.0), ("P", 7480.0),
                 ("P", 7495.0), ("P", 7510.0), ("P", 7520.0)}


# ------------------------------------------------------------------- the rig --
def _db(tmp_path, date, entries, closes, contracts=C):
    path = str(tmp_path / "bt.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trade_entries (date TEXT, entry_number INT, contracts INT,"
        " short_call_strike REAL, long_call_strike REAL,"
        " short_put_strike REAL, long_put_strike REAL,"
        " short_call_fill_price REAL, long_call_fill_price REAL,"
        " short_put_fill_price REAL, long_put_fill_price REAL)")
    conn.execute(
        "CREATE TABLE close_leg_executions (date TEXT, entry_number INT,"
        " leg_name TEXT, order_side TEXT, filled_qty INT, fill_price REAL)")
    for e in entries:
        conn.execute(
            "INSERT INTO trade_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (date, e[0], contracts, e[1], e[2], e[3], e[4],
             e[5], e[6], e[7], e[8]))
    for c in closes:
        conn.execute("INSERT INTO close_leg_executions VALUES (?,?,?,?,?,?)",
                     (date, c[0], c[1], c[2], c[3], c[4]))
    conn.commit()
    return path


def _rig(db_path, ibkr_legs=(), ledger=0.0, gross=0.0, commission=0.0):
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = False
    s.daily_state = SimpleNamespace(
        total_realized_pnl=gross, total_commission=commission)
    s._qty_mismatch_ledger = {}
    s._data_recorder = SimpleNamespace(
        _connect=lambda: sqlite3.connect(db_path))
    s.broker = MagicMock()
    s.broker.get_balance = MagicMock(return_value={
        "raw_ledger": {"USD": {"realizedpnl": ledger,
                               "netliquidationvalue": 1_000_000.0}}})
    s.broker.get_positions = MagicMock(return_value=[
        {"contractDesc": d, "realizedPnl": v, "position": 0.0}
        for d, v in ibkr_legs])
    return s


# ======================================================== the classification ==
class TestClassification:
    def test_nov12_split_is_exactly_what_ibkr_realized(self, tmp_path):
        """4 traded, 4 expired — and IBKR's list is those same 4 traded legs."""
        s = _rig(_db(tmp_path, "2026-11-12", NOV12_ENTRIES, NOV12_CLOSES))
        out = s._classify_legs_vs_expiry("2026-11-12")
        assert out["traded_keys"] == NOV12_TRADED
        assert out["expired_keys"] == NOV12_EXPIRED
        ibkr_keys = {("C" if " C " in d else "P",
                      float(d.split()[2])) for d, _ in NOV12_IBKR}
        assert ibkr_keys == NOV12_TRADED, (
            "IBKR realized exactly the legs we classify as traded — if this "
            "fails the mechanism claim is wrong, not the fixture")

    def test_nov13_predicts_all_sixteen_positions(self, tmp_path):
        """16/16, including the 7595 C whose absence was 'unexplained'."""
        s = _rig(_db(tmp_path, "2026-11-13", NOV13_ENTRIES, NOV13_CLOSES))
        out = s._classify_legs_vs_expiry("2026-11-13")
        assert out["traded_keys"] == NOV13_TRADED
        assert out["expired_keys"] == NOV13_EXPIRED
        assert len(out["traded_keys"]) + len(out["expired_keys"]) == 16

    def test_the_7595_call_is_expired_not_missing(self, tmp_path):
        """It was E#5's LONG call, never offset. Not a data gap."""
        s = _rig(_db(tmp_path, "2026-11-13", NOV13_ENTRIES, NOV13_CLOSES))
        out = s._classify_legs_vs_expiry("2026-11-13")
        assert ("C", 7595.0) in out["expired_keys"]
        assert ("C", 7595.0) not in out["traded_keys"]

    def test_a_conid_offset_by_two_entries_counts_as_traded(self, tmp_path):
        """7670 C on 11-12 had NO closing order: E#2 sold it, E#3 bought it.

        Opposing legs on one conid net to flat and IBKR realizes them, so
        'traded' must not be defined as 'has a close_leg_executions row'.
        """
        s = _rig(_db(tmp_path, "2026-11-12", NOV12_ENTRIES, NOV12_CLOSES))
        out = s._classify_legs_vs_expiry("2026-11-12")
        assert ("C", 7670.0) in out["traded_keys"]
        assert not any(c[1].endswith("call") for c in NOV12_CLOSES), \
            "fixture guard: no call leg was ever closed by order that day"

    def test_partially_offset_position_is_traded(self, tmp_path):
        """7590 C on 11-13: bought 14, sold 21. Partly offset, partly expired.

        IBKR realizes the offset part, so the position must read as traded.
        """
        s = _rig(_db(tmp_path, "2026-11-13", NOV13_ENTRIES, NOV13_CLOSES))
        out = s._classify_legs_vs_expiry("2026-11-13")
        assert ("C", 7590.0) in out["traded_keys"]


class TestArithmetic:
    def test_nov12_identity_closes_to_the_cent(self, tmp_path):
        """expired_gross + implied_commission == the logged drift, +/- 1c."""
        s = _rig(_db(tmp_path, "2026-11-12", NOV12_ENTRIES, NOV12_CLOSES))
        out = s._classify_legs_vs_expiry("2026-11-12")
        ibkr_on_traded = sum(v for _, v in NOV12_IBKR)
        implied = out["offset_gross"] - ibkr_on_traded
        drift = NOV12_OURS_GROSS - NOV12_LEDGER
        assert abs(out["expired_gross"] - (-245.0)) < 0.01
        assert abs(implied - 121.68) < 0.02
        assert abs((out["expired_gross"] + implied) - drift) <= 0.02, (
            f"identity broken: expired {out['expired_gross']} + implied "
            f"{implied} != drift {drift}")

    def test_implied_commission_per_side_is_plausible(self, tmp_path):
        """~$1.24/contract-side on a clean day. A free sanity check."""
        s = _rig(_db(tmp_path, "2026-11-12", NOV12_ENTRIES, NOV12_CLOSES))
        out = s._classify_legs_vs_expiry("2026-11-12")
        implied = out["offset_gross"] - sum(v for _, v in NOV12_IBKR)
        assert out["offset_contract_sides"] == 98
        assert 1.0 < implied / out["offset_contract_sides"] < 1.5

    def test_offset_and_expired_gross_sum_to_our_whole_day(self, tmp_path):
        """Every leg lands in exactly one bucket — nothing lost, nothing double."""
        s = _rig(_db(tmp_path, "2026-11-12", NOV12_ENTRIES, NOV12_CLOSES))
        out = s._classify_legs_vs_expiry("2026-11-12")
        total = out["offset_gross"] + out["expired_gross"]
        assert abs(total - NOV12_OURS_GROSS) < 0.01


# ================================================================= the alarm ==
class TestLegFateAlarm:
    def test_consistent_day_raises_no_error(self, tmp_path, caplog):
        s = _rig(_db(tmp_path, "2026-11-13", NOV13_ENTRIES, NOV13_CLOSES),
                 ibkr_legs=NOV13_IBKR, ledger=1995.55, gross=1205.0,
                 commission=338.10)
        with caplog.at_level("INFO"):
            HydraStrategy._reconcile_pnl_against_broker(s, "2026-11-13")
        assert "LEG-FATE OK" in caplog.text
        assert "LEG-FATE MISMATCH" not in caplog.text

    def test_a_real_orphan_does_not_trip_the_alarm(self, tmp_path, caplog):
        """7490 P was never ours. It belongs to the orphan bucket, not here."""
        s = _rig(_db(tmp_path, "2026-11-13", NOV13_ENTRIES, NOV13_CLOSES),
                 ibkr_legs=NOV13_IBKR, ledger=1995.55, gross=1205.0)
        with caplog.at_level("INFO"):
            HydraStrategy._reconcile_pnl_against_broker(s, "2026-11-13")
        assert "LEG-FATE OK" in caplog.text

    def test_ibkr_realizing_a_leg_we_booked_expired_is_an_ERROR(
            self, tmp_path, caplog):
        """The 2026-10-14 family: a traded close booked as a worthless expiry."""
        poisoned = list(NOV13_IBKR) + [
            ("SPX    NOV2026 7595 C [SPXW  261113C07595000 100]", -440.0)]
        s = _rig(_db(tmp_path, "2026-11-13", NOV13_ENTRIES, NOV13_CLOSES),
                 ibkr_legs=poisoned, ledger=1555.55, gross=1205.0)
        with caplog.at_level("INFO"):
            HydraStrategy._reconcile_pnl_against_broker(s, "2026-11-13")
        assert "LEG-FATE MISMATCH" in caplog.text
        assert any(r.levelname == "ERROR" and "LEG-FATE" in r.message
                   for r in caplog.records), "must be ERROR, not a quiet note"
        assert "('C', 7595.0)" in caplog.text

    def test_a_close_that_never_executed_is_an_ERROR(self, tmp_path, caplog):
        """We recorded 7500 P closed; IBKR realized nothing on it."""
        thinned = [(d, v) for d, v in NOV13_IBKR if "7500 P" not in d]
        s = _rig(_db(tmp_path, "2026-11-13", NOV13_ENTRIES, NOV13_CLOSES),
                 ibkr_legs=thinned, ledger=1692.56, gross=1205.0)
        with caplog.at_level("INFO"):
            HydraStrategy._reconcile_pnl_against_broker(s, "2026-11-13")
        assert "LEG-FATE MISMATCH" in caplog.text
        assert "('P', 7500.0)" in caplog.text

    def test_expiry_split_is_logged_with_both_halves(self, tmp_path, caplog):
        s = _rig(_db(tmp_path, "2026-11-12", NOV12_ENTRIES, NOV12_CLOSES),
                 ibkr_legs=NOV12_IBKR, ledger=NOV12_LEDGER,
                 gross=NOV12_OURS_GROSS, commission=144.90)
        with caplog.at_level("INFO"):
            out = HydraStrategy._reconcile_pnl_against_broker(s, "2026-11-12")
        assert "EXPIRY-SPLIT" in caplog.text
        assert out["expired_leg_gross"] == -245.0
        assert abs(out["implied_commission_total"] - 121.68) < 0.02
        assert 1.0 < out["implied_commission_per_side"] < 1.5


# ============================================================== degradation ==
class TestNeverDisturbsSettlement:
    def test_missing_tables_return_None_not_raise(self, tmp_path):
        path = str(tmp_path / "empty.db")
        sqlite3.connect(path).close()
        s = _rig(path)
        assert s._classify_legs_vs_expiry("2026-11-12") is None

    def test_no_entries_for_the_date_returns_None(self, tmp_path):
        s = _rig(_db(tmp_path, "2026-11-12", NOV12_ENTRIES, NOV12_CLOSES))
        assert s._classify_legs_vs_expiry("2026-11-11") is None

    def test_reconcile_survives_a_None_classification(self, tmp_path, caplog):
        """The pre-existing output must be unchanged when the split is absent."""
        path = str(tmp_path / "empty.db")
        sqlite3.connect(path).close()
        s = _rig(path, ibkr_legs=NOV12_IBKR, ledger=NOV12_LEDGER,
                 gross=NOV12_OURS_GROSS, commission=144.90)
        with caplog.at_level("INFO"):
            out = HydraStrategy._reconcile_pnl_against_broker(s, "2026-11-12")
        assert out["broker_realized_pnl"] == round(NOV12_LEDGER, 2)
        assert out.get("broker_realized_by_leg")
        assert "EXPIRY-SPLIT" not in caplog.text

    def test_a_skipped_side_is_not_classified(self, tmp_path):
        """No strike means no position. A skipped side must not invent one."""
        entries = [(1, None, None, 7580.0, 7575.0, None, None, 1.50, 1.20)]
        s = _rig(_db(tmp_path, "2026-11-12", entries, []))
        out = s._classify_legs_vs_expiry("2026-11-12")
        assert out["traded_keys"] == set()
        assert out["expired_keys"] == {("P", 7580.0), ("P", 7575.0)}

    def test_an_unfilled_side_is_not_classified(self, tmp_path):
        """A strike with no fill price never reached the market."""
        entries = [(1, 7680.0, 7685.0, 7580.0, 7575.0, None, None, 1.50, 1.20)]
        s = _rig(_db(tmp_path, "2026-11-12", entries, []))
        out = s._classify_legs_vs_expiry("2026-11-12")
        assert ("C", 7680.0) not in out["expired_keys"]
        assert ("P", 7580.0) in out["expired_keys"]


# ========================================= the mistake this file nearly made ==
class TestBindingAndNeighbours:
    """2026-11-14 cost seven settlement tests: a method inserted above a
    decorated `def` landed between the decorator and the function, and every
    test still passed because they all called it UNBOUND. These go through a
    real instance, and check the neighbours."""

    def test_helper_is_bound_on_a_real_instance(self, tmp_path):
        s = _rig(_db(tmp_path, "2026-11-12", NOV12_ENTRIES, NOV12_CLOSES))
        assert callable(s._classify_legs_vs_expiry)
        assert s._classify_legs_vs_expiry("2026-11-12")["offset_contract_sides"] == 98

    def test_neighbouring_decorators_are_intact(self):
        import inspect
        import bots.hydra.strategy as mod
        src = inspect.getsource(mod).split("\n")
        i = next(n for n, line in enumerate(src)
                 if "def _classify_legs_vs_expiry" in line)
        before = [line for line in src[:i] if line.strip()][-1]
        assert not before.strip().startswith("@"), (
            "a decorator was stranded above the inserted method")
        after = [line for line in src[i + 1:] if line.strip()]
        assert not any(line.strip().startswith("@def") for line in after[:1])

    @pytest.mark.parametrize("name", [
        "_reconcile_pnl_against_broker", "_classify_legs_vs_expiry"])
    def test_both_resolve_through_the_class(self, name):
        assert callable(getattr(HydraStrategy, name))
