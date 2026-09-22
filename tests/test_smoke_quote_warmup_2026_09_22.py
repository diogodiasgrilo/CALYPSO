"""The paper smoke aborted twice on a quote that was actually there.

2026-09-22, 09:29 and 09:30 ET: `broker-paper-smoke` ran armed and aborted both
times with "market data is NOT real-time ... or no ask — Check the account's
SPX-index + OPRA real-time subscriptions." Two things were wrong with that.

1. **The data was fine.** All three availability checks passed in the same run
   (`SPX 6509='R'`, `VIX='R'`, leg `'RpBd'`), and a manual `get_quote` on the
   SAME conid moments later returned bid 12.4 / ask 12.6. The failing branch was
   the missing ask, not the entitlement one.

2. **The ask was missing only because the conid was fresh.** IBKR's snapshot
   serves a metadata-only row for the first poll(s) on a conid it has not served
   before — "Snapshot warmup" in CLAUDE.md, P7-audit H10. The smoke polled once
   and believed it. Each run also picks a different ATM strike as spot moves, so
   nearly every run hits a fresh conid and fails identically: a Gate-3 blocker
   that looks like a broker problem and is not.

The message mattered as much as the bug: it named the entitlement cause first
for a failure that had nothing to do with entitlements, sending the reader to
check subscriptions that were correct.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SRC = (Path(__file__).resolve().parents[1] / "scripts" / "broker_paper_smoke.py").read_text()


class TestItRePollsAFreshConid:
    def test_a_single_empty_quote_is_not_believed(self):
        """The whole defect: one poll, then abort."""
        assert "quote warmup" in SRC, "no re-poll path in the quote resolution"
        assert re.search(r"for attempt in range\(2,\s*\d+\)", SRC), \
            "expected a bounded re-poll loop around get_quote"

    def test_the_repoll_is_bounded_and_not_a_spin(self):
        """It must give up, not hammer the market family forever."""
        m = re.search(r"for attempt in range\(2,\s*(\d+)\)", SRC)
        assert m, "re-poll loop not found"
        assert int(m.group(1)) <= 6, "re-poll budget is too large for a pre-trade gate"
        assert "time.sleep" in SRC, "re-polling without a pause just repeats the same snapshot"

    def test_it_stops_as_soon_as_an_ask_appears(self):
        """No point paying four polls when the first retry answers."""
        assert re.search(r"if q\.get\(\"ask\"\) not in \(None, 0\):\s*\n\s*break", SRC), \
            "the loop must break on the first usable ask"

    def test_time_is_actually_imported(self):
        """A NameError here would turn a warmup blip into a crash mid-gate."""
        assert re.search(r"^import time$", SRC, re.M)


class TestTheAbortMessageNamesTheRealCause:
    def test_a_missing_ask_does_not_blame_entitlements(self):
        """The 2026-09-22 diagnosis cost: subscriptions were fine and the message
        said to check them."""
        assert "This is NOT an entitlement problem" in SRC
        assert "do not go looking" in SRC

    def test_a_real_entitlement_failure_still_names_the_instrument(self):
        """The other branch must not lose information in the split — it should say
        WHICH of SPX/VIX/leg was not real-time."""
        assert "bad = [n for n, ok in" in SRC
        assert "is NOT real-time on" in SRC

    def test_the_pre_open_cause_is_spelled_out(self):
        """The first of the two failures was genuinely pre-open: options do not
        quote before 09:30 ET, and the operator should be told that rather than
        deducing it."""
        assert "options do not quote before 09:30 ET" in SRC

    def test_the_two_causes_are_no_longer_one_sentence(self):
        """Regression guard on the original wording, which OR'd two independent
        failures into a single entitlement-flavoured sentence."""
        assert "(6509 first-char != 'R') on SPX/VIX/leg, " not in SRC


class TestTheSafetyPropertiesSurvivedTheEdit:
    def test_it_still_refuses_on_a_genuine_non_realtime_read(self):
        assert "rt_spx, rt_vix, rt_leg" in SRC
        assert "_alert(False, out); return 5" in SRC

    def test_check_only_mode_still_places_nothing(self):
        assert "CHECK-ONLY complete" in SRC
        assert "NO order placed" in SRC

    def test_the_paper_account_safety_gate_is_untouched(self):
        """It must still refuse to write anywhere but a paper (DU…) account."""
        assert "safety gate OK" in SRC
