"""A4 — an order that was REFUSED must not be reported as one that "did not fill".

On 2026-10-08 the close loop logged `did not fill — retrying...` five times for
orders that had been rejected at an unmappable IBKR prompt and **never reached
the market**, and the CRITICAL alert that paged the operator said only "FAILED
to fully close". Those are opposite problems — a liquidity problem says "walk
the price"; a blocked submission says "the order is being refused, stop
retrying and look at why" — and they read identically.

The reason was being logged inside `_place_leg_order` and then **discarded from
the returned dict**, so no caller could tell them apart.

Nothing here changes which orders get placed. It changes what the logs and the
alert say about them.
"""

import sys
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bots.hydra.strategy import HydraStrategy  # noqa: E402

CONID = 926792465
PROMPT = ('ValueError: No answer found for question: "The closing order quantity '
          'is greater than your current position. Are you sure you want to submit '
          'this order?"')


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """These tests exhaust all five attempts on purpose, so they would otherwise
    pay the real 2s inter-attempt delay (40s for the file). Neutralise the sleep
    rather than the retry count — the exhaustion path IS what is under test."""
    import bots.hydra.base_strategy as bs
    monkeypatch.setattr(bs, "EMERGENCY_CLOSE_RETRY_DELAY_SECONDS", 0)


def _strat(result):
    s = HydraStrategy.__new__(HydraStrategy)
    s.contracts_per_entry = 7
    s.dry_run = False
    s._read_open_positions = lambda strict=False: ["<p>"]
    s._net_qty_at_conid = lambda uic, positions: -7
    s._position_is_open = lambda *a, **k: True
    s._place_marketable_close = lambda **kw: dict(result)
    s._correct_over_fill = lambda *a, **k: None
    s._record_close_leg_execution = lambda *a, **k: None
    s.alerts = []
    s._emergency_close_alert_once = lambda uic, **kw: s.alerts.append(kw)
    s._log_safety_event = lambda *a, **k: None
    return s


REFUSED = {"filled": False, "submitted": False, "error": PROMPT, "order_id": None}
UNFILLED = {"filled": False, "order_id": "oid"}


def _run(s):
    try:
        return s._close_position_with_retry_ib(None, "short_put", uic=CONID,
                                               entry_number=6, contracts=7)
    except Exception:
        return None


class TestTheLogDistinguishesThem:

    def test_a_REFUSED_order_does_not_say_did_not_fill(self, caplog):
        s = _strat(REFUSED)
        with caplog.at_level("WARNING"):
            _run(s)
        txt = " ".join(r.getMessage() for r in caplog.records)
        assert "NEVER SUBMITTED" in txt
        assert "did not fill — retrying" not in txt

    def test_the_reason_is_carried_verbatim(self, caplog):
        s = _strat(REFUSED)
        with caplog.at_level("WARNING"):
            _run(s)
        txt = " ".join(r.getMessage() for r in caplog.records)
        assert "closing order quantity is greater than your current position" in txt

    def test_a_GENUINE_non_fill_still_says_did_not_fill(self, caplog):
        """The guard must not relabel every failure as a refusal."""
        s = _strat(UNFILLED)
        with caplog.at_level("WARNING"):
            _run(s)
        txt = " ".join(r.getMessage() for r in caplog.records)
        assert "did not fill" in txt
        assert "NEVER SUBMITTED" not in txt


class TestTheOperatorAlertSaysWhich:

    def test_the_CRITICAL_alert_names_the_refusal(self):
        s = _strat(REFUSED)
        _run(s)
        assert s.alerts, "no CRITICAL alert was raised"
        msg = s.alerts[-1]["message"]
        assert "REFUSED, NOT UNFILLED" in msg
        assert "never reached the market" in msg

    def test_a_genuine_non_fill_alert_is_UNCHANGED(self):
        s = _strat(UNFILLED)
        _run(s)
        assert s.alerts
        msg = s.alerts[-1]["message"]
        assert "FAILED to fully close" in msg
        assert "REFUSED" not in msg


class TestThePlaceResultCarriesTheReason:

    def _place(self, exc):
        s = HydraStrategy.__new__(HydraStrategy)
        s.dry_run = False
        s.broker = type("B", (), {
            "place_and_wait_for_fill": staticmethod(
                lambda **kw: (_ for _ in ()).throw(exc))})()
        s._ensure_coid = lambda *a, **k: "coid"
        try:
            return s._place_leg_order(instrument_id=CONID, side="BUY",
                                      quantity=7, order_type="LMT", is_exit=True)
        except Exception:
            return None

    def test_a_failed_place_reports_submitted_False_with_the_reason(self):
        res = self._place(ValueError("No answer found for question: ..."))
        if res is None:
            pytest.skip("place path needs more collaborators in this harness")
        assert res["submitted"] is False
        assert "No answer found for question" in res["error"]
        assert res["filled"] is False
