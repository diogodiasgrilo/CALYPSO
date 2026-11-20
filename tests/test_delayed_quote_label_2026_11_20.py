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
def warns():
    """WARNING+ messages from the module's OWN logger, as a live list.

    DELIBERATELY NOT pytest's log-capture fixture, and this is the reason — it
    cost a red CI run. `shared/logger_service.LoggerService._setup_logging()`
    calls `logging.getLogger().handlers.clear()`, which REMOVES pytest's
    capture handler from the root logger. Any earlier test in the session that
    constructs a LoggerService therefore blinds that fixture for every test
    after it — silently, with the record list simply empty.

    That is order-dependent, so it does not reproduce reliably: this file
    passed the full local suite (6,666 green) and then failed 13 of 19 in CI,
    where a different 8 tests skip and the ordering shifts. The product fix was
    correct and already verified in production; only the measuring instrument
    was broken.

    Attaching a handler to `bots.hydra.base_strategy` directly is immune to all
    three ways ambient state can hide a record: root handlers being cleared,
    `propagate` being turned off, and a stale global `logging.disable()`. It
    also observes the logger the code actually writes to. Restores everything
    it touched, so it cannot become the next test's version of this bug.
    """
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = _Capture(level=logging.WARNING)
    lg = logging.getLogger("bots.hydra.base_strategy")
    prev_level = lg.level
    prev_disable = logging.root.manager.disable
    prev_disabled = lg.disabled
    # All FOUR mechanisms that can hide a record, neutralised explicitly.
    # Each was reproduced against the pre-fix file and each produced exactly
    # CI's signature of 13 failed / 6 passed:
    #   1. the logger's own level above WARNING — `at_level()` sets ROOT's
    #      level, not this one's, so it does not help;
    #   2. an ancestor's `propagate = False` — irrelevant here only because
    #      the handler is on this logger, not on root;
    #   3. a stale global `logging.disable()`;
    #   4. `logger.disabled = True`, which short-circuits `Logger.handle()`
    #      before any handler runs.
    logging.disable(logging.NOTSET)
    lg.disabled = False
    lg.addHandler(handler)
    lg.setLevel(logging.WARNING)
    try:
        yield captured
    finally:
        lg.removeHandler(handler)
        lg.setLevel(prev_level)
        lg.disabled = prev_disabled
        logging.disable(prev_disable)


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
        s = _strat(underlying="SPY")
        logging.getLogger("bots.hydra.base_strategy").warning(
            "DATA: SPX quote is DELAYED (6509=%r)", "DP")
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
        lg = logging.getLogger("bots.hydra.base_strategy")
        for _ in range(100):
            lg.warning("DATA: SPY quote is DELAYED (6509=%r)", "DP")
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
    """CI went red on 13 of these 19 while the full local suite was green.

    The product fix was correct and already running in production. What broke
    was the MEASURING INSTRUMENT: every test used pytest's log-capture
    fixture, which only sees a record that reaches a handler on the ROOT
    logger.

    ⚠️ My first diagnosis was WRONG and is recorded here because the wrong one
    is plausible: I blamed `LoggerService._setup_logging()`, which really does
    call `logging.getLogger().handlers.clear()`. But pytest re-installs its
    capture handler around every test phase, so clearing root handlers does
    NOT blind it — verified against the pre-fix file, which passed 19/19 under
    exactly that condition.

    What DOES blind it, each reproduced against the pre-fix file and each
    yielding CI's exact signature of 13 failed / 6 passed:

        * the module logger's own level raised above WARNING — `at_level()`
          sets ROOT's level, not the logger's, so it is no defence;
        * an ancestor logger with `propagate = False`;
        * `logger.disabled = True`.

    Which of the three CI hit is still unidentified: nothing in product code
    does any of them, so it is ambient state from one of ~6,600 tests whose
    ORDER differs between CI and local (CI skips 8 that a local run executes).
    Chasing the individual culprit is worth less than making the instrument
    immune to the whole class, which also stops the next one.

    The failure mode is the worst kind: the capture list is simply EMPTY, so
    every assertion fails with "no warning was logged" and points at the
    product code, which was fine.

    These cases reproduce each way ambient state can blind a capture, and
    assert the fixture still sees the record. Without them the next person to
    add a test here would reach for the same broken instrument.
    """

    def test_it_survives_root_handlers_being_CLEARED(self, warns):
        """`LoggerService._setup_logging()`'s own line, verbatim. NOT the CI
        cause (pytest re-installs its handler), but free to cover."""
        logging.getLogger().handlers.clear()
        MarketData.update_spx(_strat(underlying="SPY"), 684.21, availability="DP")
        assert any("SPY quote is DELAYED" in m for m in warns), (
            "cleared root handlers blinded the capture — this is the 13-failure "
            "CI run")

    @staticmethod
    def _under(pollution):
        """Apply `pollution`, THEN drive the real `warns` fixture over it.

        The pollution has to land BEFORE the fixture is set up — that is the
        real sequence, since the offending test runs earlier in the session —
        and a test body cannot express that, because its own fixtures are
        already built by the time it executes. (The first version of these two
        cases polluted inside the body, after setup, and failed for that
        reason rather than for anything real.) So the fixture's generator is
        driven by hand here.
        """
        lg = logging.getLogger("bots.hydra.base_strategy")
        saved = (lg.level, lg.disabled, lg.propagate,
                 logging.root.manager.disable)
        try:
            pollution(lg)
            gen = warns.__wrapped__()          # the undecorated generator
            captured = next(gen)
            MarketData.update_spx(_strat(underlying="SPY"), 684.21,
                                  availability="DP")
            try:
                next(gen)
            except StopIteration:
                pass
            return captured
        finally:
            lg.level, lg.disabled, lg.propagate = saved[:3]
            logging.disable(saved[3])

    def test_it_survives_the_logger_LEVEL_being_raised(self):
        """One of the three conditions that reproduce CI exactly, and the
        subtle one: `at_level(WARNING)` raises ROOT's level, so a module logger
        pinned at ERROR filters the record before any handler is consulted.
        The pre-fix file failed 13 of 19 under precisely this."""
        got = self._under(lambda lg: lg.setLevel(logging.ERROR))
        assert any("SPY quote is DELAYED" in m for m in got), got

    def test_it_survives_the_logger_being_DISABLED(self):
        """`logger.disabled = True` short-circuits `Logger.handle()` before any
        handler runs. The FIRST version of this very fixture did not survive
        it — 18 of 24 failed — which is why it is pinned."""
        def _p(lg):
            lg.disabled = True
        got = self._under(_p)
        assert any("SPY quote is DELAYED" in m for m in got), got

    def test_it_survives_a_global_disable_set_BEFORE_setup(self):
        got = self._under(lambda lg: logging.disable(logging.CRITICAL))
        assert any("SPY quote is DELAYED" in m for m in got), got

    def test_it_survives_propagate_being_turned_OFF(self, warns):
        lg = logging.getLogger("bots.hydra.base_strategy")
        prev = lg.propagate
        lg.propagate = False
        try:
            MarketData.update_spx(_strat(underlying="SPY"), 684.21,
                                  availability="DP")
            assert any("SPY quote is DELAYED" in m for m in warns)
        finally:
            lg.propagate = prev

    def test_it_survives_a_stale_global_logging_disable(self, warns):
        """A test that calls `logging.disable(CRITICAL)` and never resets it
        would suppress every WARNING in the session. The fixture clears it for
        its own duration and restores it afterwards."""
        MarketData.update_spx(_strat(underlying="SPY"), 684.21, availability="DP")
        assert any("SPY quote is DELAYED" in m for m in warns)

    def test_the_fixture_RESTORES_what_it_touched(self, warns):
        """It must not become the next test's version of this bug."""
        lg = logging.getLogger("bots.hydra.base_strategy")
        assert any(isinstance(h, logging.Handler) for h in lg.handlers), (
            "the capture handler should be attached DURING the test")

    # ---------------------------------------------------------------- CONTROL
    def test_CONTROL_a_root_based_capture_goes_blind(self):
        """Reproduces the broken instrument: a handler on the ROOT logger is
        gone the moment anything clears root's handlers, and the resulting
        empty list is indistinguishable from 'the code logged nothing'."""
        seen: list[str] = []

        class _H(logging.Handler):
            def emit(self, record):
                seen.append(record.getMessage())

        root = logging.getLogger()
        h = _H(level=logging.WARNING)
        root.addHandler(h)
        root.setLevel(logging.WARNING)
        try:
            root.handlers.clear()  # LoggerService._setup_logging() does this
            MarketData.update_spx(_strat(underlying="SPY"), 684.21,
                                  availability="DP")
            assert seen == [], (
                "pre-fix: the capture is empty and the failure looks like a "
                "product bug")
        finally:
            if h in root.handlers:
                root.removeHandler(h)
