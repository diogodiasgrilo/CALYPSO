"""The skipped-entry counterfactual mis-scored the GEX veto. Two defects.

`analyze_skipped_entry_outcomes.py` produces the evidence for a live decision —
whether the GEX strike-adjuster's veto earns its keep, on a gate that vetoed 43
entries against 39 placed. Its `--apply` mode **writes that verdict into the
database**, so a scoring error does not stay in stdout; it becomes the record.

**D1 — attribution.** The adjuster vetoes ONE SIDE. The analyzer scored THE WHOLE
ENTRY and filtered on nothing but `skip_reason LIKE '%GEX%'`, so a veto got credit
for whatever the entry avoided regardless of which side it objected to. On
2026-09-21 that produced a verdict wrong twice over: the adjuster vetoed the
**put**, the put was **never breached**, and the entry was saved by the **call**
breach — which require-both-sides caught, not GEX. Two incorrect calls scored as
one win.

**D2 — arithmetic.** A one-sided breach was modelled as a full-entry loss:
`-(pct × width × 100 × contracts)`, discarding BOTH sides' credit and taking the
CALL's width even when the PUT breached. An iron condor whose call stops out does
not forfeit the put — the put rides to expiry and keeps its credit. Since SPX
rarely breaches both sides of one condor in a session, the one-sided case is the
COMMON one, and the loss was overstated on every instance of it.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.analyze_skipped_entry_outcomes import (  # noqa: E402
    _side_pnl,
    evaluate,
    gex_vetoed_sides,
    score_gex_veto,
)

PCT, QTY = 0.40, 7          # B's acting A2 stop at its live contract count


def _row(*, sc=7800.0, lc=7805.0, sp=7700.0, lp=7695.0,
         call_credit=0.20, put_credit=0.25, date="2026-09-21", entry=1):
    return {
        "date": date, "entry_number": entry, "skip_time": f"{date} 10:15:00",
        "skip_reason": "GEX adjuster vetoed put side",
        "theoretical_short_call": sc, "theoretical_long_call": lc,
        "theoretical_short_put": sp, "theoretical_long_put": lp,
        "estimated_call_credit": call_credit, "estimated_put_credit": put_credit,
    }


def _ev(row, lo, hi):
    return evaluate(row, lo, hi, pct_of_width=PCT, contracts=QTY)


# ======================================================================
# D2 — the sides are independent
# ======================================================================

class TestEachSideIsModelledOnItsOwn:
    def test_a_call_breach_does_NOT_forfeit_the_put_credit(self):
        """The put rides to expiry and keeps its credit. The old code charged a
        full stop and discarded both sides."""
        out = _ev(_row(), lo=7750.0, hi=7810.0)          # call breached only
        assert out["call_breach"] is True and out["put_breach"] is False
        assert out["call_pnl"] == pytest.approx(-(0.40 * 5 * 100 * 7))   # -1400
        assert out["put_pnl"] == pytest.approx(0.25 * 100 * 7)           # +175
        assert out["modelled_pnl"] == pytest.approx(-1225.0)

    def test_the_old_arithmetic_overstated_the_loss_by_the_surviving_credit(self):
        """Pins the size of the defect: -1400 was written where -1225 was true,
        a 14% overstatement on this row — in the direction that flatters the
        veto, which is the direction that matters here."""
        out = _ev(_row(), lo=7750.0, hi=7810.0)
        old_value = -(PCT * 5 * 100 * QTY)
        assert out["modelled_pnl"] > old_value
        assert out["modelled_pnl"] - old_value == pytest.approx(175.0)

    def test_a_put_breach_is_priced_at_the_PUT_width(self):
        """The old code took the call's width whenever a call existed. MKT-028
        allows 60/75pt asymmetry, so this is not hypothetical."""
        out = _ev(_row(lc=7810.0, lp=7690.0), lo=7690.0, hi=7750.0)
        assert out["put_breach"] is True and out["call_breach"] is False
        assert out["put_width"] == pytest.approx(10.0)     # 7700 - 7690
        assert out["call_width"] == pytest.approx(10.0)
        assert out["put_pnl"] == pytest.approx(-(0.40 * 10 * 100 * 7))

    def test_asymmetric_widths_are_kept_apart(self):
        out = _ev(_row(lc=7860.0, lp=7690.0), lo=7690.0, hi=7750.0)
        assert out["call_width"] == pytest.approx(60.0)
        assert out["put_width"] == pytest.approx(10.0)
        # Priced on the PUT's 10pt, not the call's 60pt.
        assert out["put_pnl"] == pytest.approx(-2800.0)

    def test_both_sides_breaching_charges_both_stops(self):
        out = _ev(_row(), lo=7690.0, hi=7810.0)
        assert out["modelled_pnl"] == pytest.approx(-1400.0 - 1400.0)

    def test_neither_breaching_keeps_both_credits(self):
        out = _ev(_row(), lo=7750.0, hi=7760.0)
        assert out["modelled_pnl"] == pytest.approx((0.20 + 0.25) * 100 * 7)

    def test_a_breached_side_with_no_width_makes_the_ENTRY_unmodellable(self):
        """A stop whose size is unknown is not a stop worth zero. Better to drop
        the row than persist a partial figure as a total — `--apply` writes it."""
        out = _ev(_row(lc=None), lo=7750.0, hi=7810.0)
        assert out["call_pnl"] is None
        assert out["modelled_pnl"] is None

    def test_a_one_sided_proposal_scores_only_that_side(self):
        out = _ev(_row(sp=None, lp=None), lo=7750.0, hi=7810.0)
        assert out["put_pnl"] == 0.0
        assert out["modelled_pnl"] == pytest.approx(-1400.0)


class TestTheSidePnlPrimitive:
    def test_unbreached_keeps_its_own_credit(self):
        assert _side_pnl(False, 0.25, 5.0, pct_of_width=PCT, contracts=QTY) == \
            pytest.approx(175.0)

    def test_breached_costs_its_own_width(self):
        assert _side_pnl(True, 0.25, 5.0, pct_of_width=PCT, contracts=QTY) == \
            pytest.approx(-1400.0)

    def test_breached_with_no_width_is_None_not_zero(self):
        assert _side_pnl(True, 0.25, 0.0, pct_of_width=PCT, contracts=QTY) is None

    def test_a_missing_credit_reads_as_zero_not_as_an_error(self):
        assert _side_pnl(False, None, 5.0, pct_of_width=PCT, contracts=QTY) == 0.0


# ======================================================================
# D1 — the veto is scored against the side it objected to
# ======================================================================

def _gex_db(tmp_path, rows, *, table=True):
    p = tmp_path / "backtesting.db"
    con = sqlite3.connect(str(p))
    if table:
        con.execute(
            "CREATE TABLE gex_decisions (timestamp TEXT, date TEXT, variant TEXT, "
            "consumer TEXT, entry_number INTEGER, side TEXT, live_action TEXT)")
        con.executemany(
            "INSERT INTO gex_decisions (date, variant, consumer, entry_number, "
            "side, live_action) VALUES (?,?,?,?,?,?)", rows)
    else:
        con.execute("CREATE TABLE placeholder (x INTEGER)")
    con.commit()
    con.close()
    return str(p)


class TestTheAttributionJoin:
    def test_it_finds_the_side_the_adjuster_vetoed(self, tmp_path):
        db = _gex_db(tmp_path, [("2026-09-21", "b", "adjuster", 1, "put", "SKIP")])
        assert gex_vetoed_sides(db, "b") == {("2026-09-21", 1): {"put"}}

    @pytest.mark.parametrize("action,expected", [
        ("SKIP", {"put"}), ("skip", {"put"}),
        ("SHIFT", None), ("KEEP", None),
    ])
    def test_only_a_SKIP_is_a_veto(self, tmp_path, action, expected):
        """SHIFT moved the strike and still placed; KEEP did nothing. Neither
        vetoed anything, so neither can be credited with a save."""
        db = _gex_db(tmp_path, [("2026-09-21", "b", "adjuster", 1, "put", action)])
        got = gex_vetoed_sides(db, "b").get(("2026-09-21", 1))
        assert got == expected

    def test_the_overlay_consumer_is_not_the_adjuster(self, tmp_path):
        """`gex_decisions` carries both consumers. The overlay is the hedge
        arming decision, not a strike veto."""
        db = _gex_db(tmp_path, [("2026-09-21", "b", "overlay", 1, "put", "SKIP")])
        assert gex_vetoed_sides(db, "b") == {}

    def test_another_variant_is_not_mixed_in(self, tmp_path):
        db = _gex_db(tmp_path, [("2026-09-21", "c", "adjuster", 1, "put", "SKIP")])
        assert gex_vetoed_sides(db, "b") == {}

    def test_both_sides_vetoed_is_representable(self, tmp_path):
        db = _gex_db(tmp_path, [
            ("2026-09-21", "b", "adjuster", 1, "put", "SKIP"),
            ("2026-09-21", "b", "adjuster", 1, "call", "SKIP"),
        ])
        assert gex_vetoed_sides(db, "b") == {("2026-09-21", 1): {"call", "put"}}

    def test_a_missing_table_is_unavailable_not_empty(self, tmp_path):
        """Pre-schema-v16 databases have no gex_decisions. The caller must say
        "cannot attribute", never "nothing was vetoed"."""
        db = _gex_db(tmp_path, [], table=False)
        assert gex_vetoed_sides(db, "b") == {}


class TestTheVetoVerdict:
    """The 2026-09-21 case, which is why this exists."""

    def test_a_veto_of_a_side_that_never_breached_is_WRONG(self):
        out = _ev(_row(), lo=7750.0, hi=7810.0)      # call breached, put did not
        assert score_gex_veto(out, {"put"}) == "wrong"

    def test_and_the_entry_was_still_saved_by_the_OTHER_side(self):
        """Both facts are true at once, and the old report collapsed them into a
        single win for GEX. The entry avoided a loss; the veto was wrong about
        where the danger was."""
        out = _ev(_row(), lo=7750.0, hi=7810.0)
        assert out["breached"] is True                  # the entry would have hurt
        assert score_gex_veto(out, {"put"}) == "wrong"  # but not for GEX's reason

    def test_a_veto_of_a_side_that_did_breach_is_CORRECT(self):
        out = _ev(_row(), lo=7750.0, hi=7810.0)
        assert score_gex_veto(out, {"call"}) == "correct"

    def test_no_attribution_row_is_neither_correct_nor_wrong(self):
        """Silence must not be scored. An unattributed veto counts in the
        dollars and abstains from the verdict."""
        out = _ev(_row(), lo=7750.0, hi=7810.0)
        assert score_gex_veto(out, set()) == "unattributed"

    def test_a_quiet_session_makes_every_veto_wrong_and_that_is_the_point(self):
        """Nothing breached, so no veto objected to a real danger — vetoing cost
        the credit. This is the measurement the gate has to survive."""
        out = _ev(_row(), lo=7750.0, hi=7760.0)
        assert score_gex_veto(out, {"put"}) == "wrong"
        assert score_gex_veto(out, {"call"}) == "wrong"
        assert out["modelled_pnl"] > 0                  # vetoing COST us

    def test_a_both_sides_veto_is_correct_if_either_breached(self):
        out = _ev(_row(), lo=7750.0, hi=7810.0)
        assert score_gex_veto(out, {"call", "put"}) == "correct"


class TestWhatGetsPersisted:
    """`--apply` writes `theoretical_pnl`; a scoring error becomes the record."""

    def test_the_persisted_number_is_the_per_side_sum(self):
        out = _ev(_row(), lo=7750.0, hi=7810.0)
        assert out["modelled_pnl"] == out["call_pnl"] + out["put_pnl"]

    def test_an_unmodellable_row_is_excluded_from_writing(self):
        """`main` filters on `modelled_pnl is not None` — pinned here so the
        None contract cannot be quietly changed to 0.0."""
        out = _ev(_row(lc=None), lo=7750.0, hi=7810.0)
        assert out["modelled_pnl"] is None

    def test_breached_is_still_the_entry_level_fact(self):
        """`would_have_stopped` describes the ENTRY, not the vetoed side — that
        is correct and must not be changed to the side-level fact."""
        out = _ev(_row(), lo=7750.0, hi=7810.0)
        assert out["breached"] is True
