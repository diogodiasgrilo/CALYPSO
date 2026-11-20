"""Variants E and H logged "SPX quote is DELAYED" about a SPY quote, 9,114 times.

Found 2026-11-20. `update_spx` is an SPX-shaped NAME on a parameterized
fleet: E and H both run `underlying_symbol: "SPY"` with `sec_type: "STK"`,
and they call the same `update_spx`. So every freshness warning they emitted
named the wrong instrument.

Measured per day from their own logs:

    2026-11-16   E   855   H   580
    2026-11-17   E 1,692   H 1,044
    2026-11-18   E 1,739   H 1,088
    2026-11-19   E 1,299   H   817
                 ------------------
                 E 5,585   H 3,529   = 9,114 lines in four sessions

A/B/C/bl emitted ZERO, which is the control that makes it a mechanism rather
than a coincidence: the live seat trades SPX and its quotes are real-time.

TWO defects, one line:

1. THE LABEL. The lag itself is real and already understood — this account
   holds the index real-time subscription but not a US-equity one, so SPY
   returns 'DP' by design (see CLAUDE.md Troubleshooting "Cause 2"). Only the
   SYMBOL was wrong, which is the worse half: an operator grepping for a
   delayed SPX feed would find 9,114 hits belonging to two dry-run variants
   trading a different instrument. Same class as the 2026-10-07 "log lines
   must DERIVE, not assert" finding.

2. THE FLOOD. This branch had no dedup, and unlike the Z/Y/N branch twenty
   lines above it it does not `return` — a permanently-delayed feed is a
   steady state here, not an incident, so it logged on every ~10s tick for the
   whole session. That is 25% of E's entire log output and 23% of H's. The
   Z/Y/N branch's own comment already said "this would otherwise flood every
   ~10s tick"; the same was true one branch down and nobody had looked.

NOT changed: control flow. The price is still accepted and the freshness clock
still advances on a 'D' flag — only the log line is rate-limited. A test below
pins that, because suppressing a warning must never become suppressing the
data it describes.
"""

from __future__ import annotations

import logging
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from bots.hydra.base_strategy import MarketData, MEICStrategy  # noqa: E402


def _strat(underlying=None, volatility=None):
    """A real MarketData driving the REAL update_spx / update_vix bodies.

    `update_spx` is a MarketData method, not a strategy one — which is the
    whole reason the first attempt at this fix did nothing. Built here exactly
    as MEICStrategy.__init__ builds it, so the labels travel the same path.
    """
    kw = {}
    if underlying is not None:
        kw["underlying_symbol"] = underlying
    if volatility is not None:
        kw["volatility_symbol"] = volatility
    return MarketData(**kw)



@pytest.fixture
def warns(monkeypatch):
    """Messages the module WOULD log, by replacing its `logger` object.

    DELIBERATELY NOT the logging framework, in any form, and it took three CI
    runs to earn that. The subject under test is the MESSAGE CONTENT and the
    dedup arithmetic — not Python's logging plumbing — so routing the
    assertions through that plumbing only imports its ambient global state.

    What was tried and failed:

    1. pytest's `caplog`. Passed the full local suite (6,666 green); CI failed
       13 of 19 with an empty record list, which reads as "the code logged
       nothing" and points at product code that was fine.
    2. A handler attached directly to `bots.hydra.base_strategy`, with the
       logger's level, `disabled` flag and the global `logging.disable()` all
       neutralised. Locally that survived every stressor I could construct —
       raised level, `propagate = False`, `disabled = True`, cleared root
       handlers — each of which reproduced CI's exact 13-failed/6-passed
       signature against version 1. CI failed it anyway, and an attached
       handler seeing nothing rules out all four of those mechanisms.

    So the CI cause remains UNIDENTIFIED, and saying so is the honest position:
    nothing in product code touches logger state, the suite has ~6,600 tests,
    and CI skips 8 that a local run executes, so ordering differs. Rather than
    guess a fourth time, this removes the dependency on the thing that varies.

    Swapping the module's `logger` is hermetic: it drives the REAL `update_spx`
    / `update_vix` bodies, the REAL format strings, the REAL
    `_underlying_label()` and the REAL dedup window, and renders each message
    exactly as logging would (`msg % args`). `monkeypatch` restores it.
    """
    import bots.hydra.base_strategy as _bs

    captured: list[str] = []

    class _Recorder:
        @staticmethod
        def _render(msg, args):
            try:
                return str(msg) % args if args else str(msg)
            except Exception:  # a bad format string is itself a defect worth seeing
                return f"{msg!r} % {args!r}"

        def warning(self, msg, *args, **kw):
            captured.append(self._render(msg, args))

        # ERROR/CRITICAL captured too: a freshness problem that escalated
        # should not vanish from a test that is checking freshness messages.
        error = critical = exception = warning

        def info(self, *a, **k): pass
        def debug(self, *a, **k): pass

    monkeypatch.setattr(_bs, "logger", _Recorder())
    return captured


# ══════════════════════════════════════════════════════ 1. the label
class TestTheLabelIsDerived:
    def test_a_SPY_variant_says_SPY(self, warns):
        """E and H. This is the 9,114-line defect."""
        s = _strat(underlying="SPY")
        if True:
            MarketData.update_spx(s, 684.21, availability="DP")
        msgs = warns
        assert any("SPY quote is DELAYED" in m for m in msgs), msgs
        assert not any("SPX quote is DELAYED" in m for m in msgs), (
            f"still naming SPX on a SPY variant: {msgs}")

    def test_an_SPX_variant_still_says_SPX(self, warns):
        """The guard rail: A/B/C/bl must read exactly as before."""
        s = _strat(underlying="SPX")
        if True:
            MarketData.update_spx(s, 7176.99, availability="DP")
        assert any("SPX quote is DELAYED" in m for m in warns)

    def test_an_instance_with_no_symbol_set_falls_back_to_SPX(self, warns):
        """A bare instance can reach a log site before
        `_load_instrument_params` runs. A label lookup must never be able to
        break a data-freshness warning."""
        s = _strat()  # no underlying_symbol at all
        if True:
            MarketData.update_spx(s, 7176.99, availability="DP")
        assert any("SPX quote is DELAYED" in m for m in warns)

    def test_the_NOT_real_time_branch_is_also_derived(self, warns):
        """The Z/Y/N sibling had the same hardcoded symbol. It happens not to
        fire on E/H today, which is exactly why it would have been missed."""
        s = _strat(underlying="SPY")
        if True:
            MarketData.update_spx(s, 684.21, availability="Z")
        msgs = warns
        assert any("SPY quote is NOT real-time" in m for m in msgs), msgs

    def test_the_vix_branches_are_derived_too(self, warns):
        s = _strat(volatility="VXN")
        if True:
            MarketData.update_vix(s, 21.4, availability="DP")
        assert any("VXN quote is DELAYED" in m for m in warns)

    def test_vix_defaults_to_VIX(self, warns):
        """H sets no volatility_symbol — it must still read VIX."""
        s = _strat(underlying="SPY")
        if True:
            MarketData.update_vix(s, 19.6, availability="DP")
        assert any("VIX quote is DELAYED" in m for m in warns)

    # ---------------------------------------------------------------- CONTROL
    def test_CONTROL_a_hardcoded_label_lies_on_a_SPY_variant(self, warns):
        """Reproduces the defect: the message asserts the symbol instead of
        deriving it, so a SPY variant reports a delayed SPX feed."""
        import bots.hydra.base_strategy as _bs
        s = _strat(underlying="SPY")
        # Emitted through the module's own logger attribute — the same object
        # the production line uses — so the control exercises the real path.
        _bs.logger.warning("DATA: SPX quote is DELAYED (6509=%r)", "DP")
        assert s.underlying_symbol == "SPY"
        assert any("SPX quote is DELAYED" in m for m in warns), (
            "the hardcoded line names SPX while the instrument is SPY")
        # the hardcoded line names SPX while the instrument is SPY
        assert "SPX" != s.underlying_symbol


# ══════════════════════════════════════════════════════ 2. the flood
class TestItIsDeduped:
    def test_a_hundred_delayed_ticks_log_ONCE(self, warns):
        """E emitted 1,739 of these in a single session."""
        s = _strat(underlying="SPY")
        if True:
            for _ in range(100):
                MarketData.update_spx(s, 684.21, availability="DP")
        n = sum("quote is DELAYED" in m for m in warns)
        assert n == 1, f"logged {n} times across 100 delayed ticks"

    def test_the_vix_branch_is_deduped_too(self, warns):
        s = _strat()
        if True:
            for _ in range(100):
                MarketData.update_vix(s, 19.6, availability="DP")
        n = sum("quote is DELAYED" in m for m in warns)
        assert n == 1, f"logged {n} times"

    def test_the_suppression_is_DISCLOSED(self, warns):
        """A silently-suppressed warning is how a real one gets missed. The
        line has to say it is rate-limited."""
        s = _strat(underlying="SPY")
        if True:
            MarketData.update_spx(s, 684.21, availability="DP")
        msg = [m for m in warns if "DELAYED" in m][0]
        assert "suppressed" in msg.lower(), msg

    def test_it_logs_again_after_the_window(self, warns):
        """Suppression is a window, not a one-shot mute — a feed still delayed
        half an hour later must say so again."""
        s = _strat(underlying="SPY")
        if True:
            MarketData.update_spx(s, 684.21, availability="DP")
            s._last_spx_delayed_warn_at -= 301  # pretend 5 min elapsed
            MarketData.update_spx(s, 684.30, availability="DP")
        n = sum("quote is DELAYED" in m for m in warns)
        assert n == 2, f"expected 2 across the window boundary, got {n}"

    # ─────────────────── the part that must NOT have changed
    def test_the_PRICE_is_still_accepted_on_every_suppressed_tick(self):
        """THE INVARIANT. Rate-limiting the message must never rate-limit the
        data: 'D' means delayed-but-usable, so the price is taken and the
        freshness clock advances. If this ever fails, the dedup has turned a
        log change into a data outage — and DATA-001 would then trip on a feed
        that was working."""
        s = _strat(underlying="SPY")
        for i in range(50):
            MarketData.update_spx(s, 684.0 + i, availability="DP")
        assert s.spx_price == 684.0 + 49, "the last delayed tick was dropped"
        assert s.last_spx_update is not None, "freshness clock did not advance"
        assert len(s.price_history) == 50, (
            "velocity history lost ticks — flash-crash detection would blind")

    def test_a_FROZEN_tick_still_refuses_the_price(self):
        """The other half of the invariant: Z/Y/N must still NOT advance the
        clock. Proof the dedup edit did not blur the two branches together."""
        s = _strat(underlying="SPY")
        MarketData.update_spx(s, 684.21, availability="Z")
        assert s.spx_price == 0.0, "a frozen tick was accepted as a price"
        assert s.last_spx_update is None, "a frozen tick advanced the clock"

    # ---------------------------------------------------------------- CONTROL
    def test_CONTROL_without_dedup_every_tick_logs(self, warns):
        """Reproduces the flood on the same input shape."""
        # Emitted on the logger UNDER TEST, not this module's, so it travels
        # the same path the real line does — otherwise the control proves
        # nothing about the mechanism it is supposed to be a control for.
        import bots.hydra.base_strategy as _bs
        for _ in range(100):
            _bs.logger.warning("DATA: SPY quote is DELAYED (6509=%r)", "DP")
        n = sum("quote is DELAYED" in m for m in warns)
        assert n == 100, "pre-fix, one line per tick for the whole session"


# ═══════════════════════════════════════ 3. the WIRING, which is what broke
class TestTheStrategyActuallyPassesTheLabel:
    """The seam, pinned on its own.

    Everything above tests MarketData in isolation and would keep passing if
    `MEICStrategy.__init__` stopped forwarding the configured symbols — which
    is precisely the bug the first attempt at this fix shipped: the helper read
    `self.underlying_symbol` on a MarketData instance that has no such field,
    so it returned the "SPX" default on every variant and changed nothing. The
    isolated tests all passed. Only driving the real constructor finds it.
    """

    def _build(self, cfg_symbol):
        """Runs the REAL `_load_instrument_params` then the REAL MarketData
        construction, in that order, the way __init__ does."""
        class _C(MEICStrategy):
            def _calculate_strikes(self, *a, **k): ...
            def _check_stop_losses(self, *a, **k): ...
            def _initiate_entry(self, *a, **k): ...

        s = _C.__new__(_C)
        s.strategy_config = {"underlying_symbol": cfg_symbol}
        MEICStrategy._load_instrument_params(s)
        # the two lines from __init__, verbatim in effect
        s.market_data = MarketData(
            underlying_symbol=str(getattr(s, "underlying_symbol", None) or "SPX"),
            volatility_symbol=str(getattr(s, "volatility_symbol", None) or "VIX"),
        )
        return s

    def test_a_SPY_config_reaches_the_log_label(self, warns):
        s = self._build("SPY")
        assert s.underlying_symbol == "SPY"
        assert s.market_data.underlying_symbol == "SPY", (
            "the config value never reached MarketData — this is the shape of "
            "the fix that did nothing")
        if True:
            MarketData.update_spx(s.market_data, 684.21, availability="DP")
        assert any("SPY quote is DELAYED" in m for m in warns)

    def test_an_SPX_config_is_unchanged(self, warns):
        s = self._build("SPX")
        assert s.market_data.underlying_symbol == "SPX"
        if True:
            MarketData.update_spx(s.market_data, 7176.99, availability="DP")
        assert any("SPX quote is DELAYED" in m for m in warns)

    def test_a_config_with_no_symbol_defaults_to_SPX(self):
        class _C(MEICStrategy):
            def _calculate_strikes(self, *a, **k): ...
            def _check_stop_losses(self, *a, **k): ...
            def _initiate_entry(self, *a, **k): ...

        s = _C.__new__(_C)
        s.strategy_config = {}
        MEICStrategy._load_instrument_params(s)
        assert s.underlying_symbol == "SPX"

    def test_the_real_init_forwards_them(self):
        """Reads the constructor call site itself — not a grep for a string,
        but the actual keyword names the source passes, so a rename or removal
        fails here."""
        import inspect
        src = inspect.getsource(MEICStrategy.__init__)
        i = src.index("self.market_data = MarketData(")
        call = src[i:i + 400]
        assert "underlying_symbol=" in call, (
            "__init__ no longer forwards the underlying label to MarketData; "
            "every variant would silently log SPX again")
        assert "volatility_symbol=" in call

    # ---------------------------------------------------------------- CONTROL
    def test_CONTROL_an_unforwarded_MarketData_logs_SPX_on_a_SPY_variant(
            self, warns):
        """The exact bug that shipped and was caught: strategy knows SPY,
        MarketData was never told, log says SPX."""
        md = MarketData()  # built without the labels, as before the fix
        assert md.underlying_symbol == "SPX"
        if True:
            MarketData.update_spx(md, 684.21, availability="DP")
        assert any("SPX quote is DELAYED" in m for m in warns), (
            "pre-fix shape: a SPY price reported as a delayed SPX quote")


# ═════════════════════════════ 4. the instrument, not the thing measured
class TestTheCaptureSurvivesAmbientLoggingState:
    """CI went red twice on these tests while the full local suite was green.

    Both times the product fix was correct and already running in production;
    what failed was the MEASURING INSTRUMENT, with the worst possible symptom —
    an empty capture, which reads as "the code logged nothing" and accuses
    product code that is fine.

    Attempt 1 used pytest's `caplog` (13 of 19 failed in CI). Attempt 2
    attached a handler to the module's logger with its level, `disabled` flag
    and the global `logging.disable()` all neutralised; locally that survived
    every stressor I could build, and CI failed it anyway — which rules out all
    of those mechanisms, since an attached handler would have seen the record.

    The CI cause is UNIDENTIFIED. Attempt 3 therefore does not depend on the
    logging framework at all: it swaps the module's `logger` object. These
    cases assert that immunity directly, so nobody reintroduces a
    framework-dependent capture here and rediscovers this the slow way.
    """

    def test_it_is_immune_to_the_global_logging_disable(self, warns):
        logging.disable(logging.CRITICAL)
        try:
            MarketData.update_spx(_strat(underlying="SPY"), 684.21,
                                  availability="DP")
            assert any("SPY quote is DELAYED" in m for m in warns)
        finally:
            logging.disable(logging.NOTSET)

    def test_it_is_immune_to_root_handlers_being_cleared(self, warns):
        logging.getLogger().handlers.clear()
        MarketData.update_spx(_strat(underlying="SPY"), 684.21,
                              availability="DP")
        assert any("SPY quote is DELAYED" in m for m in warns)

    def test_it_is_immune_to_the_logger_being_DISABLED(self, warns):
        logging.getLogger("bots.hydra.base_strategy").disabled = True
        try:
            MarketData.update_spx(_strat(underlying="SPY"), 684.21,
                                  availability="DP")
            assert any("SPY quote is DELAYED" in m for m in warns)
        finally:
            logging.getLogger("bots.hydra.base_strategy").disabled = False

    def test_it_is_immune_to_the_logger_LEVEL_being_raised(self, warns):
        """The mechanism that reproduced CI's signature against attempt 1:
        `caplog.at_level(WARNING)` raises ROOT's level, not the module
        logger's, so a logger pinned at ERROR filters the record first."""
        lg = logging.getLogger("bots.hydra.base_strategy")
        prev = lg.level
        lg.setLevel(logging.ERROR)
        try:
            MarketData.update_spx(_strat(underlying="SPY"), 684.21,
                                  availability="DP")
            assert any("SPY quote is DELAYED" in m for m in warns)
        finally:
            lg.setLevel(prev)

    def test_it_is_immune_to_a_FILTER_on_the_logger(self, warns):
        """Not a confirmed CI cause, but the one remaining mechanism that can
        drop a record after the level and `disabled` checks pass — and it is
        why attempt 2's reasoning was incomplete."""
        lg = logging.getLogger("bots.hydra.base_strategy")

        class _DropAll(logging.Filter):
            def filter(self, record):
                return False

        f = _DropAll()
        lg.addFilter(f)
        try:
            MarketData.update_spx(_strat(underlying="SPY"), 684.21,
                                  availability="DP")
            assert any("SPY quote is DELAYED" in m for m in warns)
        finally:
            lg.removeFilter(f)

    def test_the_message_is_RENDERED_as_logging_would(self, warns):
        """The recorder must apply `msg % args`, or a `%s` placeholder would
        pass a label assertion while the real log line said literally '%s'."""
        MarketData.update_spx(_strat(underlying="SPY"), 684.21,
                              availability="DP")
        msg = [m for m in warns if "DELAYED" in m][0]
        assert "%s" not in msg, msg
        assert "SPY" in msg and "'DP'" in msg, msg

    # ---------------------------------------------------------------- CONTROL
    def test_CONTROL_a_framework_capture_can_be_silenced(self):
        """Reproduces the class: any capture that depends on the logging
        framework can be blinded by ambient state, and the result is an empty
        list indistinguishable from 'nothing was logged'."""
        seen: list[str] = []

        class _H(logging.Handler):
            def emit(self, record):
                seen.append(record.getMessage())

        lg = logging.getLogger("bots.hydra.base_strategy")
        h = _H(level=logging.WARNING)
        lg.addHandler(h)
        lg.setLevel(logging.WARNING)
        prev_disabled = lg.disabled
        lg.disabled = True          # one of several ways; all look identical
        try:
            MarketData.update_spx(_strat(underlying="SPY"), 684.21,
                                  availability="DP")
            assert seen == [], (
                "pre-fix: the capture is empty and the failure looks like a "
                "product bug")
        finally:
            lg.disabled = prev_disabled
            lg.removeHandler(h)


# ════════════════════════ 5. the REAL cause of three red CI runs
class TestTheFirstWarningAfterBoot:
    """`time.monotonic()` is seconds since BOOT, and 0.0 is not "never".

    THE ACTUAL BUG, found only because CI kept failing. The dedup compared
    `monotonic() - getattr(self, attr, 0.0) >= 300`. On a freshly-started host
    that subtraction is the machine's UPTIME, so under 300 seconds it reads as
    "warned recently" and the FIRST warning is silently swallowed — the one
    that matters most, a bot coming up against a bad feed.

    This is why three CI runs went red while the full local suite passed:
    this laptop had 23 days of uptime (monotonic 1,989,553) and a GitHub runner
    has seconds. Every test expecting a warning failed; every test expecting
    none passed; and `test_it_logs_again_after_the_window` failed with
    `AttributeError: no attribute '_last_spx_delayed_warn_at'` — the timestamp
    was never even set, which should have pointed here immediately instead of
    at three successive theories about the logging framework.

    ⚠️ Those three theories were ALL WRONG (caplog's root handler, the logger's
    level/`disabled`/filters, LoggerService clearing handlers). The log capture
    was never broken. Recorded because the wrong diagnoses were each plausible
    and each cost a push.
    """

    @staticmethod
    def _at_uptime(seconds, fn):
        """Run `fn` with the clock pinned to a machine `seconds` old."""
        import time as _time
        real = _time.monotonic
        _time.monotonic = lambda: float(seconds)
        try:
            return fn()
        finally:
            _time.monotonic = real

    def test_the_first_delayed_warning_fires_on_a_FRESH_BOOT(self, warns):
        """150s of uptime — a CI runner, or our own VM right after a reboot."""
        md = _strat(underlying="SPY")
        self._at_uptime(150.0, lambda: MarketData.update_spx(
            md, 684.21, availability="DP"))
        assert any("SPY quote is DELAYED" in m for m in warns), (
            "swallowed on a fresh boot — the exact shape of three red CI runs")
        assert md._last_spx_delayed_warn_at is not None

    def test_the_first_NOT_real_time_warning_fires_on_a_fresh_boot(self, warns):
        """The Z/Y/N branch carried the same defect and PREDATES today — so a
        bot restarted into a frozen or unsubscribed feed stayed silent about it
        for five minutes. That is the branch whose dedup this one was modelled
        on, which is how the flaw was inherited."""
        md = _strat(underlying="SPY")
        self._at_uptime(150.0, lambda: MarketData.update_spx(
            md, 684.21, availability="Z"))
        assert any("SPY quote is NOT real-time" in m for m in warns)

    def test_both_VIX_branches_fire_on_a_fresh_boot(self, warns):
        md = _strat()
        self._at_uptime(150.0, lambda: MarketData.update_vix(
            md, 19.6, availability="DP"))
        md2 = _strat()
        self._at_uptime(150.0, lambda: MarketData.update_vix(
            md2, 19.6, availability="Z"))
        assert sum("VIX quote is DELAYED" in m for m in warns) == 1
        assert sum("VIX quote is NOT real-time" in m for m in warns) == 1

    def test_it_still_SUPPRESSES_inside_the_window_after_boot(self, warns):
        """The guard rail: fixing the first-warning case must not disable the
        throttle. 100 ticks at 150s uptime still log exactly once."""
        md = _strat(underlying="SPY")
        self._at_uptime(150.0, lambda: [
            MarketData.update_spx(md, 684.21, availability="DP")
            for _ in range(100)])
        assert sum("quote is DELAYED" in m for m in warns) == 1

    def test_zero_uptime_is_handled(self, warns):
        """The degenerate case: `monotonic()` of exactly 0.0. With a 0.0
        default this compared as `0.0 - 0.0 = 0 < 300` and suppressed."""
        md = _strat(underlying="SPY")
        self._at_uptime(0.0, lambda: MarketData.update_spx(
            md, 684.21, availability="DP"))
        assert any("SPY quote is DELAYED" in m for m in warns)

    # ---------------------------------------------------------------- CONTROL
    def test_CONTROL_the_zero_default_swallows_it(self, warns):
        """Reproduces the defect exactly: `0.0` as "never warned" means any
        machine with under 5 minutes of uptime loses its first warning."""
        uptime, window, never_warned_default = 150.0, 300, 0.0
        assert uptime - never_warned_default < window, (
            "this inequality IS the bug: 150 - 0.0 = 150 < 300, so the code "
            "concluded it had warned recently when it had never warned at all")
        # and with None there is nothing to subtract, so the branch cannot lie
        assert None is not never_warned_default
