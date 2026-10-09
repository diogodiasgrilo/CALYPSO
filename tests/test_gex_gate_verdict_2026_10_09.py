"""The registered GEX verdict must implement the PREREGISTRATION, not a variant of it.

Written the same day as the script, with n at 24 of 25 — i.e. while the answer
was still unknowable. These tests exist because a pre-registered test is only
worth the arithmetic that executes it, and that arithmetic gets exactly one
honest chance to be written.

The headline test is `test_sample_size_table_matches_the_prereg`: the document
states, as its justification for n = 25, that a continued 0% breach rate
reaches p < 0.01 against a 20% base at **21**, and needs **29** at a 15% base.
Those three numbers are derivable from the binomial alone, so reproducing them
proves this script's statistics ARE the document's. If that test ever fails,
either the script's arithmetic drifted or the prereg's sample-size reasoning
was wrong — both worth stopping for.
"""

import importlib.util
import io
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "gex_gate_verdict", ROOT / "scripts" / "gex_gate_verdict.py")
gv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gv)


class TestTheRegisteredConstants:
    """These are the test. Changing one silently changes what was agreed."""

    def test_cutoff_is_exclusive_and_window_starts_the_next_day(self):
        assert gv.CUTOFF_EXCLUSIVE == "2026-09-29"
        assert gv.WINDOW_START == "2026-09-30"

    def test_required_n_and_alpha(self):
        assert gv.REQUIRED_N == 25
        assert gv.ALPHA == 0.01

    def test_benchmark_band_and_registered_variant(self):
        assert gv.BENCHMARK_BAND_PT == 10.0
        assert gv.REGISTERED_VARIANT == "b"


class TestTheStatisticsAreThePreregsStatistics:

    def test_binom_cdf_basics(self):
        assert gv._binom_cdf(5, 5, 0.5) == pytest.approx(1.0)
        assert gv._binom_cdf(0, 1, 0.5) == pytest.approx(0.5)
        assert gv._binom_cdf(0, 10, 0.2) == pytest.approx(0.8 ** 10)

    def test_sample_size_table_matches_the_prereg(self):
        """The prereg justifies n=25 with: a continued 0% reaches p < 0.01 at
        n=21 against a 20% base, and n=29 against a 15% base. Both must fall
        out of THIS binomial, or the script is not running the agreed test."""
        def smallest_n(base: float) -> int:
            n = 1
            while gv._binom_cdf(0, n, base) >= gv.ALPHA:
                n += 1
                assert n < 500
            return n
        assert smallest_n(0.20) == 21, "prereg says 21 at a 20% base"
        assert smallest_n(0.15) == 29, "prereg says 29 at a 15% base"

    def test_required_n_clears_the_20pct_base(self):
        """25 must be enough for the headline claim it was chosen for."""
        assert gv._binom_cdf(0, gv.REQUIRED_N, 0.20) < gv.ALPHA

    def test_p_is_one_sided_toward_SAFER(self):
        """The hypothesis is 'vetoed strikes are NOT more dangerous', so the
        p-value must shrink as observed breaches FALL, never as they rise."""
        assert gv._binom_cdf(0, 25, 0.2) < gv._binom_cdf(5, 25, 0.2)
        assert gv._binom_cdf(25, 25, 0.2) == pytest.approx(1.0)


class TestBreachIsAMarketFact:

    def test_a_call_breaches_when_the_high_reaches_it(self):
        assert gv._breached("call", 7800, 7810, 7700) is True
        assert gv._breached("call", 7800, 7799, 7700) is False

    def test_a_put_breaches_when_the_low_reaches_it(self):
        assert gv._breached("put", 7700, 7810, 7690) is True
        assert gv._breached("put", 7700, 7810, 7701) is False

    def test_touching_exactly_counts_as_a_breach(self):
        assert gv._breached("call", 7800, 7800, 7700) is True
        assert gv._breached("put", 7700, 7810, 7700) is True

    def test_a_missing_session_is_UNSCORABLE_not_safe(self):
        """Treating an unscorable day as 'no breach' would bias the vetoed arm
        toward looking correct — the exact direction the test is measuring."""
        assert gv._breached("call", 7800, None, 7700) is None
        assert gv._breached("put", 7700, 7810, None) is None


def _make_db(tmp_path, rows, ohlc, variant="b"):
    """rows: (date, side, spot, strike, action). ohlc: {date: (high, low)}."""
    d = tmp_path / "data" / f"variant_{variant}"
    d.mkdir(parents=True)
    db = d / "backtesting.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE gex_decisions (timestamp TEXT, date TEXT, variant TEXT,
                   consumer TEXT, entry_number INTEGER, side TEXT, spot REAL,
                   reference_strike REAL, live_action TEXT)""")
    con.execute("""CREATE TABLE daily_summaries (date TEXT, spx_high REAL, spx_low REAL)""")
    for i, (date, side, spot, strike, action) in enumerate(rows):
        con.execute("INSERT INTO gex_decisions VALUES (?,?,?,?,?,?,?,?,?)",
                    (date + " 12:00:00", date, variant, "adjuster", i, side, spot, strike, action))
    for date, (hi, lo) in ohlc.items():
        con.execute("INSERT INTO daily_summaries VALUES (?,?,?)", (date, hi, lo))
    con.commit()
    con.close()
    return str(tmp_path)


def _run(root, *argv):
    out = io.StringIO()
    sys.argv = ["gex_gate_verdict", "--root", root, *argv]
    with redirect_stdout(out):
        gv.main()
    return out.getvalue()


class TestGuard1_BlindBelowN:
    """'Not permitted: stopping early on a favourable count' cannot be obeyed
    by someone who already knows the direction."""

    def test_below_n_it_refuses_AND_hides_the_rates(self, tmp_path):
        rows = [("2026-10-01", "put", 7800, 7750, "SKIP")] * 5
        root = _make_db(tmp_path, rows, {"2026-10-01": (7810, 7700)})
        out = _run(root)
        assert "NO VERDICT" in out
        assert "breached" not in out.lower()
        assert "VERDICT: **" not in out

    def test_it_still_reports_the_ACCRUAL(self, tmp_path):
        """Knowing how far along you are is protocol; knowing which way it
        leans is not."""
        rows = [("2026-10-01", "put", 7800, 7750, "SKIP")] * 3
        root = _make_db(tmp_path, rows, {"2026-10-01": (7810, 7700)})
        out = _run(root)
        assert "3 of 25" in out
        assert "accrual by date" in out

    def test_in_sample_decisions_are_EXCLUDED(self, tmp_path):
        """Everything on or before the 2026-09-29 cutoff formed the hypothesis."""
        rows = ([("2026-09-29", "put", 7800, 7750, "SKIP")] * 30 +
                [("2026-10-01", "put", 7800, 7750, "SKIP")] * 2)
        root = _make_db(tmp_path, rows, {"2026-09-29": (7810, 7700), "2026-10-01": (7810, 7700)})
        out = _run(root)
        assert "2 of 25" in out, "pre-cutoff rows leaked into the out-of-sample count"


class TestTheDecisionRule:

    def _scenario(self, tmp_path, vetoed_breaches, kept_breaches, n_vetoed=25, n_kept=40):
        """Vetoed puts at 7750; kept puts at 7745 (within the +/-10pt band).
        A breach is forced by giving that date a low beneath the strike."""
        rows, ohlc = [], {}
        for i in range(n_vetoed):
            day = "2026-10-%02d" % (1 + i % 20)
            rows.append((day, "put", 7800.0, 7750.0, "SKIP"))
        for i in range(n_kept):
            day = "2026-11-%02d" % (1 + i % 20)
            rows.append((day, "put", 7800.0, 7745.0, "KEEP"))
        # dates breach in order, so the counts are exact
        v_days = sorted({r[0] for r in rows if r[4] == "SKIP"})
        k_days = sorted({r[0] for r in rows if r[4] == "KEEP"})
        for idx, day in enumerate(v_days):
            n_on_day = sum(1 for r in rows if r[0] == day and r[4] == "SKIP")
            ohlc[day] = (7810, 7700) if vetoed_breaches > 0 else (7810, 7760)
            if vetoed_breaches > 0:
                vetoed_breaches -= n_on_day
        for day in k_days:
            n_on_day = sum(1 for r in rows if r[0] == day and r[4] == "KEEP")
            ohlc[day] = (7810, 7700) if kept_breaches > 0 else (7810, 7760)
            if kept_breaches > 0:
                kept_breaches -= n_on_day
        return _make_db(tmp_path, rows, ohlc)

    def test_zero_vetoed_breaches_against_a_high_benchmark_DISABLES(self, tmp_path):
        root = self._scenario(tmp_path, vetoed_breaches=0, kept_breaches=40)
        out = _run(root)
        assert "DISABLE the SKIP action" in out

    def test_vetoed_breaching_MORE_than_benchmark_KEEPS(self, tmp_path):
        root = self._scenario(tmp_path, vetoed_breaches=25, kept_breaches=0)
        out = _run(root)
        assert "KEEP the gate" in out
        assert "recorded FAILED" in out

    def test_a_LOWER_rate_that_is_not_significant_still_KEEPS(self, tmp_path):
        """The prereg demands BOTH safer-or-equal AND p < 0.01. Lower-but-noisy
        is a KEEP, and this is the case most likely to be fudged."""
        root = self._scenario(tmp_path, vetoed_breaches=10, kept_breaches=20)
        out = _run(root)
        assert "KEEP the gate" in out
        assert "not separable from chance" in out


class TestGuard2_OneVariant:

    def test_a_non_registered_variant_is_branded_exploratory(self, tmp_path):
        rows = [("2026-10-01", "put", 7800, 7750, "SKIP")] * 3
        root = _make_db(tmp_path, rows, {"2026-10-01": (7810, 7700)}, variant="bl")
        out = _run(root, "--variant", "bl")
        assert "NOT the registered variant" in out
        assert "EXPLORATORY" in out

    def test_pooling_is_branded_a_protocol_deviation(self, tmp_path):
        rows = [("2026-10-01", "put", 7800, 7750, "SKIP")] * 3
        root = _make_db(tmp_path, rows, {"2026-10-01": (7810, 7700)}, variant="b")
        d = Path(root) / "data" / "variant_bl"
        d.mkdir(parents=True)
        src = sqlite3.connect(Path(root) / "data" / "variant_b" / "backtesting.db")
        dst = sqlite3.connect(d / "backtesting.db")
        src.backup(dst)
        src.close(); dst.close()
        out = _run(root, "--pool", "b,bl")
        assert "PROTOCOL DEVIATION" in out

    def test_the_default_is_the_registered_variant_with_no_banner(self, tmp_path):
        rows = [("2026-10-01", "put", 7800, 7750, "SKIP")] * 3
        root = _make_db(tmp_path, rows, {"2026-10-01": (7810, 7700)})
        out = _run(root)
        assert "PROTOCOL DEVIATION" not in out
        assert "EXPLORATORY" not in out


class TestGuard3_NoPnL:

    def test_the_script_cannot_score_dollars(self):
        src = (ROOT / "scripts" / "gex_gate_verdict.py").read_text()
        body = src.split('"""', 2)[-1]          # ignore the docstring, which discusses it
        for token in ("theoretical_pnl", "net_pnl", "credit", "total_credit"):
            assert token not in body, f"{token} appears in the body — the prereg scores BREACH"
