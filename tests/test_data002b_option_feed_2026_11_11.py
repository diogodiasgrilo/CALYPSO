"""DATA-002b + the DATA-002 session-gate fix (2026-11-11).

Two defects, one file.

1. SESSION GATE. `_refresh_market_data_health` was gated on
   `MarketData._is_regular_session_or_later()`, which means "at-or-after 9:30
   TODAY" and stays True all evening. So after every close it evaluated the
   broker's legitimately FROZEN quotes, flagged the feed degraded, and painted
   "Trading HALTED" on the dashboard until the next day's reset. Observed live
   on `bl`: md_degraded True, "SPX feed is FROZEN", since 22:02:03.

2. OPTION (OPRA) FEED. DATA-002 watched SPX and VIX only. When the account's
   OPRA subscription lapsed on 2026-11-02, option quotes went 6509='DPBd'
   (delayed 15-20 min) and the halt did not fire:

       Oct 20-27   15 attempts    0 failed     0 delayed-option reads
       Nov 02-10   30 attempts   30 failed   343 delayed-option reads

   Every short leg sat unfilled at a limit computed from a stale quote.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from bots.hydra.base_strategy import MEICStrategy, MarketData


# ---------------------------------------------------------------- session gate

def _at(hh, mm, *, day="2026-11-11"):
    return datetime.fromisoformat(f"{day} {hh:02d}:{mm:02d}:00")


SESSION_CASES = [
    # (time, expected_live, why)
    ((9, 29), False, "pre-open"),
    ((9, 30), True, "the bell"),
    ((13, 0), True, "midday"),
    ((15, 59), True, "last minute"),
    ((16, 0), False, "the close is exclusive"),
    ((22, 2), False, "THE DEFECT: the evening that painted the banner red"),
    ((23, 59), False, "late evening"),
]


@pytest.mark.parametrize("hhmm,expected,why", SESSION_CASES)
def test_session_is_live_excludes_after_hours(hhmm, expected, why):
    with patch("bots.hydra.base_strategy.get_us_market_time",
               return_value=_at(*hhmm)):
        assert MarketData._session_is_live() is expected, why


def test_the_two_helpers_actually_disagree_after_the_close():
    """The whole point: the old gate says True at 22:02 and the new one says False.

    If this ever stops failing apart, the fix has been silently reverted to an
    alias and every case above would pass against the defect.
    """
    with patch("bots.hydra.base_strategy.get_us_market_time",
               return_value=_at(22, 2)):
        assert MarketData._is_regular_session_or_later() is True
        assert MarketData._session_is_live() is False


def test_session_is_live_false_on_weekend():
    # 2026-11-14 is a Saturday.
    with patch("bots.hydra.base_strategy.get_us_market_time",
               return_value=_at(13, 0, day="2026-11-14")):
        assert MarketData._session_is_live() is False


def test_refresh_is_gated_on_the_live_session_helper():
    """The call site must use the NEW helper, not the old one."""
    import inspect
    fn = MEICStrategy._refresh_market_data_health
    src = inspect.getsource(fn)
    # Strip the docstring: it NAMES the old helper deliberately, to explain the
    # distinction. Only the executable body may reference it.
    body = src.replace(fn.__doc__ or "", "")
    assert "_session_is_live()" in body
    assert "_is_regular_session_or_later" not in body, (
        "the refresh must not be gated on the at-or-after-9:30 helper")


# ------------------------------------------------------------ option feed (b)

class _Stub:
    """Minimal carrier for the option-feed observation methods."""
    MARKET_DATA_REALTIME = MEICStrategy.MARKET_DATA_REALTIME
    OPTION_FEED_OBSERVATION_MAX_AGE_S = MEICStrategy.OPTION_FEED_OBSERVATION_MAX_AGE_S
    note_option_feed_availability = MEICStrategy.note_option_feed_availability
    option_feed_degraded = MEICStrategy.option_feed_degraded

    def __init__(self):
        self.market_data = MarketData()


def _stub_at(hh, mm):
    return patch("bots.hydra.base_strategy.get_us_market_time",
                 return_value=_at(hh, mm))


@pytest.mark.parametrize("flag,should_flag,label", [
    ("R", False, "real-time"),
    ("DPBd", True, "THE INCIDENT: delayed snapshot+book, 11-02 onward"),
    ("D", True, "delayed"),
    ("Z", True, "frozen"),
    ("N", True, "not subscribed"),
])
def test_option_availability_classification(flag, should_flag, label):
    s = _Stub()
    with _stub_at(13, 0):
        s.note_option_feed_availability(flag)
        reason = s.option_feed_degraded()
    assert bool(reason) is should_flag, label
    if should_flag:
        assert "OPRA" in reason and "exits unaffected" in reason


def test_delayed_names_the_ibkr_lag_so_an_operator_can_act():
    s = _Stub()
    with _stub_at(13, 0):
        s.note_option_feed_availability("DPBd")
        assert "15-20 min" in s.option_feed_degraded()


def test_absent_flag_does_not_overwrite_a_known_bad_reading():
    """IBKR does not always populate 6509. 'Unknown' must not clear 'bad'."""
    s = _Stub()
    with _stub_at(13, 0):
        s.note_option_feed_availability("DPBd")
        for empty in (None, ""):
            s.note_option_feed_availability(empty)
            assert s.option_feed_degraded(), f"{empty!r} erased the signal"


def test_unobserved_feed_fails_open():
    s = _Stub()
    with _stub_at(13, 0):
        assert s.option_feed_degraded() is None


def test_observation_expires_so_a_morning_read_cannot_speak_for_the_afternoon():
    s = _Stub()
    with _stub_at(12, 0):
        s.note_option_feed_availability("DPBd")
    with _stub_at(12, 19):           # inside the 20-min window
        assert s.option_feed_degraded() is not None
    with _stub_at(12, 41):           # outside it
        assert s.option_feed_degraded() is None


def test_recovery_clears_the_flag():
    s = _Stub()
    with _stub_at(13, 0):
        s.note_option_feed_availability("DPBd")
        assert s.option_feed_degraded()
        s.note_option_feed_availability("R")
        assert s.option_feed_degraded() is None


# ------------------------------------------------- the veto is where it must be

def test_veto_sits_in_the_credit_gate_right_after_the_estimate():
    """Placement is load-bearing, so pin it.

    The veto must read a FRESH observation — i.e. come after
    `_estimate_entry_credit`, which is what reads the option quotes. A cached
    check in the entry gate would DEADLOCK: a blocked entry never reads an
    option, so the flag could never clear.
    """
    import inspect
    from bots.hydra.strategy import HydraStrategy
    src = inspect.getsource(HydraStrategy._check_credit_gate)
    est = src.index("self._estimate_entry_credit(entry)")
    veto = src.index("self.option_feed_degraded()")
    assert est < veto, "veto must come AFTER the estimate that refreshes the observation"
    assert "option_feed_not_realtime" in src


def test_entry_gate_does_NOT_gate_on_the_option_feed():
    """The deadlock guard, asserted as a negative."""
    import inspect
    from bots.hydra.strategy import HydraStrategy
    src = inspect.getsource(HydraStrategy._initiate_entry)
    assert "option_feed_degraded" not in src, (
        "gating entries on a cached option observation deadlocks: no entry "
        "means no option read means the flag never clears"
    )


def test_both_option_readers_record_the_observation():
    """The entry path prices off the BATCH reader, so it must record too."""
    import inspect
    from bots.hydra.strategy import HydraStrategy
    for fn in (HydraStrategy._read_option_quote,
               HydraStrategy._read_option_quotes_batch):
        assert "note_option_feed_availability" in inspect.getsource(fn), fn.__name__
