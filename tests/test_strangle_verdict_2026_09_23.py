"""The long-strangle page must state the session's RESULT correctly.

Variant H's page reduces a session to two sentences — did SPX break the band,
and what was the position worth — and on H's very first live day both were
wrong in ways that pointed the operator at the opposite conclusion.

**Defect 1 — the wrong side reported.** 2026-09-23 ran 7695.13 – 7760.02 against
a 7760 call / 7720 put. BOTH sides broke: the call was touched by 0.02pt, the
put was driven 24.87pt through. The code checked ``brokeCall`` first, so the
page read "Broke the call side by 0.0pt" — a near-miss, on a day the put
finished twenty-five points in the money.

**Defect 2 — a mid-session mark called a settlement.** The counterfactual said
"it would have settled at ..." from whatever the last tick happened to be. SPXW
is cash-settled at the close, so that sentence is true after 16:00 ET and false
before it: intraday the legs are worth their intrinsic value PLUS the time value
still in them, so the figure is a floor, not a result.

WHY THESE TESTS RUN THE TYPESCRIPT INSTEAD OF READING IT
---------------------------------------------------------
Most of this repo's frontend tests assert on the SOURCE — they grep for a class
name or a constant. That is the right tool for "is this token present", and the
wrong one here: both defects above were live in code that contained every
correct-looking token. The arithmetic had to be *evaluated* to be caught.

So the logic lives in ``lib/strangleVerdict.ts`` as pure functions with no React
or recharts imports, and these tests execute it under Node's type-stripping. It
is the same lesson as the CI break of 2026-09-23 recorded in the memory gate —
*"if CI runs something, the local suite must run it too, not import it"* — one
layer down.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LIB = ROOT / "dashboard" / "frontend" / "src" / "lib" / "strangleVerdict.ts"
PAGE = ROOT / "dashboard" / "frontend" / "src" / "pages" / "LongStrangle.tsx"

#: The real session. Read from variant B's market_ticks on 2026-09-23; the
#: strikes are the ones H's own skip row proposed and declined.
SESSION_LOW = 7695.13
SESSION_HIGH = 7760.02
SESSION_CLOSE = 7704.59
CALL_K = 7760.0
PUT_K = 7720.0
DEBIT = 710.0

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the TS helpers"
)


def _run_ts(body: str):
    """Execute ``body`` against the real helper module and return its JSON.

    Node ≥22.6 strips the type annotations natively, so the shipped ``.ts`` file
    runs unmodified — no build step, no second copy of the logic to drift.
    """
    script = (
        f"import {{ bandVerdict, valueStrangleAt, sessionIsSettled, todayInNewYork }} "
        f"from {json.dumps(str(LIB))};\n{body}\n"
    )
    tmp = ROOT / "tests" / "_strangle_verdict_probe.mjs.ts"
    tmp.write_text(script)
    try:
        r = subprocess.run(
            [shutil.which("node"), "--no-warnings", str(tmp)],
            capture_output=True, text=True, timeout=60,
        )
        assert r.returncode == 0, f"node failed:\n{r.stderr}"
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        tmp.unlink(missing_ok=True)


def _verdict(hi=SESSION_HIGH, lo=SESSION_LOW, call=CALL_K, put=PUT_K):
    return _run_ts(
        f"console.log(JSON.stringify(bandVerdict({hi}, {lo}, {call}, {put})));")


class TestTheVerdictReportsTheWidestBreach:
    """The 2026-09-23 defect, pinned on the numbers that produced it."""

    def test_the_real_session_reports_the_PUT_side(self):
        v = _verdict()
        assert v["widestSide"] == "put"
        assert v["widest"] == pytest.approx(24.87, abs=0.01)

    def test_it_does_not_report_the_0_02pt_call_touch_as_the_headline(self):
        """The literal regression. A 0.02pt touch and a 24.87pt drive-through
        are not interchangeable, and the smaller one must never lead."""
        v = _verdict()
        assert v["widest"] != pytest.approx(0.02, abs=0.01)
        assert v["callThrough"] == pytest.approx(0.02, abs=0.01)

    @pytest.mark.parametrize("hi,lo", [
        (SESSION_HIGH, SESSION_LOW),   # the real session — put leads
        (7790.0, 7719.0),              # call leads, both broke
        (7800.0, 7730.0),              # call only
        (7750.0, 7700.0),              # put only
        (7755.0, 7725.0),              # neither
    ])
    def test_the_headline_number_always_describes_the_headline_SIDE(self, hi, lo):
        """The invariant the page's sentence depends on, stated directly.

        "Broke the {side} side by {n}pt" is a single claim, and the defect was
        that ``side`` and ``n`` were derived independently — so the sentence
        could name one side and measure the other. Checking each separately can
        never catch that; checking that they AGREE does.
        """
        v = _verdict(hi=hi, lo=lo)
        if not v["broke"]:
            assert v["widest"] == 0
            return
        through = v["callThrough"] if v["widestSide"] == "call" else v["putThrough"]
        assert v["widest"] == pytest.approx(through)

    def test_both_breaches_are_still_reported(self):
        """Leading with the widest must not HIDE the other side — the page
        prints it in the parenthetical."""
        v = _verdict()
        assert v["brokeCall"] is True and v["brokePut"] is True
        assert v["otherSide"] == "call"
        assert v["otherThrough"] == pytest.approx(0.02, abs=0.01)

    def test_a_call_only_break_reports_the_call(self):
        v = _verdict(hi=7800.0, lo=7730.0)
        assert v["widestSide"] == "call" and v["brokePut"] is False
        assert v["widest"] == pytest.approx(40.0)

    def test_a_put_only_break_reports_the_put(self):
        v = _verdict(hi=7750.0, lo=7700.0)
        assert v["widestSide"] == "put" and v["brokeCall"] is False
        assert v["widest"] == pytest.approx(20.0)

    def test_a_call_led_two_sided_break_reports_the_call(self):
        """The mirror of the real session — proves the fix is not just a
        hard-coded preference for the put side."""
        v = _verdict(hi=7790.0, lo=7719.0)
        assert v["widestSide"] == "call"
        assert v["widest"] == pytest.approx(30.0)
        assert v["otherSide"] == "put" and v["otherThrough"] == pytest.approx(1.0)

    def test_an_untouched_band_breaks_nothing_and_reports_the_near_miss(self):
        v = _verdict(hi=7755.0, lo=7725.0)
        assert v["broke"] is False and v["widest"] == 0
        # 5pt short on the call, 5pt short on the put — closest approach is 5.
        assert v["closestApproach"] == pytest.approx(5.0)

    def test_closest_approach_is_zero_once_anything_broke(self):
        assert _verdict()["closestApproach"] == 0

    def test_touching_a_strike_exactly_is_not_a_break(self):
        """At-the-money settles worthless. A strangle needs to go THROUGH."""
        v = _verdict(hi=CALL_K, lo=PUT_K)
        assert v["broke"] is False
        assert v["closestApproach"] == pytest.approx(0.0)


class TestTheSettlementCounterfactual:
    """"A veto happened" is not a result. "The veto cost $831" is."""

    def _value(self, spx=SESSION_CLOSE, settled="true", debit=DEBIT):
        return _run_ts(
            f"console.log(JSON.stringify(valueStrangleAt("
            f"{spx}, {CALL_K}, {PUT_K}, {debit}, {settled})));")

    def test_the_real_session_cost_831_dollars_to_decline(self):
        """7704.59 leaves the 7720 put 15.41pt in the money = $1,541 against a
        $710 debit. Declining the entry gave up $831."""
        v = self._value()
        assert v["intrinsic"] == pytest.approx(1541.0, abs=0.5)
        assert v["pnl"] == pytest.approx(831.0, abs=0.5)
        assert v["pctOfDebit"] == pytest.approx(117.0, abs=0.5)

    def test_a_close_inside_the_band_loses_the_whole_debit(self):
        """The defining property of the structure: worst case IS the debit."""
        v = self._value(spx=7740.0)
        assert v["intrinsic"] == 0.0
        assert v["pnl"] == pytest.approx(-DEBIT)
        assert v["pctOfDebit"] == pytest.approx(-100.0)

    def test_only_the_in_the_money_leg_contributes(self):
        """Both legs are long, so nothing can go negative — the OTM leg
        contributes zero, never a subtraction."""
        assert self._value(spx=7800.0)["intrinsic"] == pytest.approx(4000.0)
        assert self._value(spx=7700.0)["intrinsic"] == pytest.approx(2000.0)

    def test_no_debit_means_no_percentage_rather_than_a_divide_by_zero(self):
        assert self._value(debit=0)["pctOfDebit"] is None


class TestASessionInProgressIsNotASettlement:
    """SPXW is cash-settled at the close. Before it, intrinsic is a FLOOR."""

    def _settled(self, date, today, label, close="16:00"):
        return _run_ts(
            f"console.log(JSON.stringify(sessionIsSettled("
            f'"{date}", "{today}", "{label}", "{close}")));')

    def test_a_past_session_has_settled_whatever_its_last_tick_says(self):
        """The feed stops where it stops; a closed day is closed."""
        assert self._settled("2026-09-22", "2026-09-23", "11:42") is True

    def test_today_before_the_close_has_NOT_settled(self):
        assert self._settled("2026-09-23", "2026-09-23", "11:42") is False

    def test_today_at_the_close_has_settled(self):
        assert self._settled("2026-09-23", "2026-09-23", "16:00") is True

    def test_an_early_close_day_settles_at_its_own_close(self):
        """A half session must not read as 'still running' for three hours."""
        assert self._settled("2026-11-27", "2026-11-27", "13:00", close="13:00") is True

    def test_a_missing_date_does_not_claim_settlement(self):
        """Fails toward the hedged sentence, which is true either way."""
        assert self._settled("", "2026-09-23", "16:00") is False

    def test_the_page_renders_a_DIFFERENT_sentence_for_each_case(self):
        """The flag has to reach the copy. Both branches must exist, and the
        open-session one must carry the intrinsic-only caveat — otherwise the
        page states a settlement that has not happened.

        Strings updated 2026-09-29 when the single verdict sentence became a
        two-row comparison (peak vs close). The INTENT is unchanged and is
        what is asserted: a settled session and an open one must not read the
        same, and an open one must still say the value is intrinsic-only.
        """
        src = PAGE.read_text()
        # the branch still exists and is driven by the same flag
        assert "settled ?" in src
        # settled and open produce DIFFERENT labels
        assert "At the close" in src
        assert "Latest print" in src
        # and the open-session caveat survives verbatim
        assert "intrinsic only — the session is still open" in src

    def test_a_settled_session_is_not_described_as_the_outcome(self):
        """2026-09-29. The panel used to show ONLY the settlement value for a
        declined entry, which scores the veto as if H held to expiry. It does
        not — both real trades exited on profit targets (19m, 175m), and the
        table below scores those at their ACTUAL exits. Two yardsticks on one
        screen, and the veto always looked better for it.

        So a settled session must show the session PEAK as well, and must say
        the close is a floor rather than the result.
        """
        src = PAGE.read_text()
        assert "const peakBeatsClose" in src, "the session peak is not computed"
        # RENDERED, not merely computed. Gating the row off (`{false && (`)
        # left every grep for the identifier intact and the first version of
        # this test passed against it.
        assert "value={peak.intrinsic}" in src and "pnl={peak.pnl}" in src, (
            "the peak is computed but never passed to a row")
        assert "{peakBeatsClose && (" in src, (
            "the peak row is not gated on peakBeatsClose — it is either always "
            "on or switched off")
        assert "At its best" in src, "the peak row carries no label"
        assert "profit targets" in src and "floor" in src, (
            "the page does not tell the reader that H exits on targets, so "
            "the close is a floor rather than the outcome")

    def test_the_peak_is_labelled_intrinsic_only(self):
        """The peak uses intrinsic, but the real position also held time
        value — so it is a FLOOR on what it was worth, and overstating it
        would be the same error in the other direction."""
        src = PAGE.read_text()
        i = src.index("At its best")
        assert "intrinsic only" in src[i:i + 600], (
            "the peak row does not say it is intrinsic-only")

    def test_todayInNewYork_returns_an_iso_date(self):
        """The API's dates are YYYY-MM-DD and the comparison is a string
        compare, so any other rendering silently breaks every case above."""
        import re
        today = _run_ts("console.log(JSON.stringify(todayInNewYork()));")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", today), today


class TestTheLogicIsExtractedSoItCanBeRun:
    """The reason this file can exist at all."""

    def test_the_helper_imports_nothing(self):
        """No React, no recharts — otherwise it cannot be executed under node
        and these tests degrade back into grepping the source.

        Matches import STATEMENTS, not the word: a prose 'imports nothing' in
        the module's own docstring tripped the first version of this test, which
        is the same docstring-grep trap that bit the inherited-gate audit
        earlier the same day.
        """
        offenders = [ln for ln in LIB.read_text().splitlines()
                     if ln.lstrip().startswith(("import ", "require(", "from "))]
        assert not offenders, offenders

    def test_the_page_uses_the_helper_rather_than_its_own_copy(self):
        src = PAGE.read_text()
        assert "from \"../lib/strangleVerdict\"" in src
        # The inline arithmetic must be GONE, not merely shadowed — two copies
        # of a result calculation is how the first defect survived review.
        assert "const brokeCall = " not in src
        assert "const settleValue =" not in src


class TestThePageDescribesTheVariantItActuallyIs:
    """Copy that states a variant's OPERATING MODE has to track the taxonomy.

    The page said "dry-run shadow". H's status is ``dry_run_locked``, and in
    this fleet those are different things: a shadow variant (A, C) merely has
    ``dry_run: true`` in config and can be flipped by editing it, while a locked
    one REFUSES to construct outside dry-run. The page named the weaker of the
    two — undersells the guarantee, and would go silently stale the day H is
    promoted.

    Derived from ``strategy_taxonomy``, not asserted as a literal, for the
    reason the CI break of the same morning made expensive: a guard naming the
    thing it guards protects only that thing.
    """

    def test_the_disclaimer_matches_H_s_taxonomy_status(self):
        import shared.strategy_taxonomy as tax
        status = tax.STRATEGIES["h"].status
        src = PAGE.read_text()
        expected = {
            "dry_run_locked": "dry-run LOCKED",
            "dry_run_shadow": "dry-run shadow",
            "live": "LIVE",
        }[status]
        assert expected in src, (
            f"H's taxonomy status is {status!r}, so the page's disclaimer must "
            f"say {expected!r}. Promoting a variant means updating what its page "
            f"claims about it.")

    def test_the_page_does_not_still_claim_H_is_uninstalled(self):
        """It was true when the page was written and stopped being true the same
        day: H was installed on the VM on 2026-09-23 and recorded a row that
        morning. A docstring that describes ``available: false`` as the expected
        response sends the next reader looking for a bug that is not there."""
        src = PAGE.read_text()
        assert "is not installed on the" not in src
        assert "`available: false` is the expected response today" not in src


class TestNetMeansNet:
    """2026-09-29. The running-record cards read `realized_pnl` — which is
    GROSS — and labelled the total "Net". On a strategy risking $83-$102, a
    $4.60 round trip is ~5% of the debit, so the headline and every per-trade
    return were overstated. A card labelled Net must not be a gross number.
    """

    def test_the_reader_selects_commissions(self):
        """A frontend cannot subtract a field the API never sends."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "dashboard" / "backend" / "services" / "ls_reader.py").read_text()
        i = src.index("def read_ls_recent")
        assert "x.commissions" in src[i:i + 2000], (
            "read_ls_recent does not select commissions, so Net cannot be net")

    def test_the_page_subtracts_them(self):
        """Pins the BINDING, not the helper.

        The first version asserted only that a `netOf` helper existed and
        subtracted fees. Reverting the `net` total to `realized_pnl` left
        `netOf` in place (it is still used for the win count), so the test
        passed against the restored bug — the exact blind-grep failure this
        repo has hit before. Assert what the Net card is actually summing.
        """
        src = PAGE.read_text()
        assert "const netOf" in src, "no net helper on the page"
        i = src.index("const netOf")
        assert "commissions" in src[i:i + 200] or "commOf" in src[i:i + 200], (
            "netOf does not subtract commissions")
        assert "const net = scored.reduce((a, r) => a + netOf(r), 0)" in src, (
            "the Net total is not summed through netOf — it is reading the "
            "gross column again")
        assert "const wins = scored.filter((r) => netOf(r) > 0)" in src, (
            "the win count is computed on gross, so a trade whose fees "
            "exceed its gain would still count as a win")

    def test_the_net_card_shows_its_decomposition(self):
        """Showing the gross and the fees beside the net is what makes the
        correction visible rather than silent."""
        src = PAGE.read_text()
        i = src.index('label="Net"')
        assert "gross" in src[i:i + 300] and "fees" in src[i:i + 300]

    def test_return_on_debit_is_recomputed_not_read_from_the_gross_column(self):
        """`pnl_pct_of_debit` is stored gross for the same reason. Reading it
        would reintroduce the bug in the percentage while fixing the dollars.
        """
        src = PAGE.read_text()
        i = src.index("const avgPct")
        window = src[i:i + 400]
        assert "netOf" in window, "avg return still reads the gross column"

    def test_a_thin_sample_says_so(self):
        """'100% — 2 of 2 — source claims ~80%' invites a comparison that two
        trades cannot support."""
        src = PAGE.read_text()
        assert "thin" in src and "too few to compare" in src
