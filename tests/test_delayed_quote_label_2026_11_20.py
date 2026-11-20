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



def _warns(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING]


# ══════════════════════════════════════════════════════ 1. the label
class TestTheLabelIsDerived:
    def test_a_SPY_variant_says_SPY(self, caplog):
        """E and H. This is the 9,114-line defect."""
        s = _strat(underlying="SPY")
        with caplog.at_level(logging.WARNING):
            MarketData.update_spx(s, 684.21, availability="DP")
        msgs = _warns(caplog)
        assert any("SPY quote is DELAYED" in m for m in msgs), msgs
        assert not any("SPX quote is DELAYED" in m for m in msgs), (
            f"still naming SPX on a SPY variant: {msgs}")

    def test_an_SPX_variant_still_says_SPX(self, caplog):
        """The guard rail: A/B/C/bl must read exactly as before."""
        s = _strat(underlying="SPX")
        with caplog.at_level(logging.WARNING):
            MarketData.update_spx(s, 7176.99, availability="DP")
        assert any("SPX quote is DELAYED" in m for m in _warns(caplog))

    def test_an_instance_with_no_symbol_set_falls_back_to_SPX(self, caplog):
        """A bare instance can reach a log site before
        `_load_instrument_params` runs. A label lookup must never be able to
        break a data-freshness warning."""
        s = _strat()  # no underlying_symbol at all
        with caplog.at_level(logging.WARNING):
            MarketData.update_spx(s, 7176.99, availability="DP")
        assert any("SPX quote is DELAYED" in m for m in _warns(caplog))

    def test_the_NOT_real_time_branch_is_also_derived(self, caplog):
        """The Z/Y/N sibling had the same hardcoded symbol. It happens not to
        fire on E/H today, which is exactly why it would have been missed."""
        s = _strat(underlying="SPY")
        with caplog.at_level(logging.WARNING):
            MarketData.update_spx(s, 684.21, availability="Z")
        msgs = _warns(caplog)
        assert any("SPY quote is NOT real-time" in m for m in msgs), msgs

    def test_the_vix_branches_are_derived_too(self, caplog):
        s = _strat(volatility="VXN")
        with caplog.at_level(logging.WARNING):
            MarketData.update_vix(s, 21.4, availability="DP")
        assert any("VXN quote is DELAYED" in m for m in _warns(caplog))

    def test_vix_defaults_to_VIX(self, caplog):
        """H sets no volatility_symbol — it must still read VIX."""
        s = _strat(underlying="SPY")
        with caplog.at_level(logging.WARNING):
            MarketData.update_vix(s, 19.6, availability="DP")
        assert any("VIX quote is DELAYED" in m for m in _warns(caplog))

    # ---------------------------------------------------------------- CONTROL
    def test_CONTROL_a_hardcoded_label_lies_on_a_SPY_variant(self, caplog):
        """Reproduces the defect: the message asserts the symbol instead of
        deriving it, so a SPY variant reports a delayed SPX feed."""
        s = _strat(underlying="SPY")
        logging.getLogger(__name__).warning(
            "DATA: SPX quote is DELAYED (6509=%r)", "DP")
        assert s.underlying_symbol == "SPY"
        # the hardcoded line names SPX while the instrument is SPY
        assert "SPX" != s.underlying_symbol


# ══════════════════════════════════════════════════════ 2. the flood
class TestItIsDeduped:
    def test_a_hundred_delayed_ticks_log_ONCE(self, caplog):
        """E emitted 1,739 of these in a single session."""
        s = _strat(underlying="SPY")
        with caplog.at_level(logging.WARNING):
            for _ in range(100):
                MarketData.update_spx(s, 684.21, availability="DP")
        n = sum("quote is DELAYED" in m for m in _warns(caplog))
        assert n == 1, f"logged {n} times across 100 delayed ticks"

    def test_the_vix_branch_is_deduped_too(self, caplog):
        s = _strat()
        with caplog.at_level(logging.WARNING):
            for _ in range(100):
                MarketData.update_vix(s, 19.6, availability="DP")
        n = sum("quote is DELAYED" in m for m in _warns(caplog))
        assert n == 1, f"logged {n} times"

    def test_the_suppression_is_DISCLOSED(self, caplog):
        """A silently-suppressed warning is how a real one gets missed. The
        line has to say it is rate-limited."""
        s = _strat(underlying="SPY")
        with caplog.at_level(logging.WARNING):
            MarketData.update_spx(s, 684.21, availability="DP")
        msg = [m for m in _warns(caplog) if "DELAYED" in m][0]
        assert "suppressed" in msg.lower(), msg

    def test_it_logs_again_after_the_window(self, caplog):
        """Suppression is a window, not a one-shot mute — a feed still delayed
        half an hour later must say so again."""
        s = _strat(underlying="SPY")
        with caplog.at_level(logging.WARNING):
            MarketData.update_spx(s, 684.21, availability="DP")
            s._last_spx_delayed_warn_at -= 301  # pretend 5 min elapsed
            MarketData.update_spx(s, 684.30, availability="DP")
        n = sum("quote is DELAYED" in m for m in _warns(caplog))
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
    def test_CONTROL_without_dedup_every_tick_logs(self, caplog):
        """Reproduces the flood on the same input shape."""
        with caplog.at_level(logging.WARNING):
            for _ in range(100):
                logging.getLogger(__name__).warning(
                    "DATA: SPY quote is DELAYED (6509=%r)", "DP")
        n = sum("quote is DELAYED" in m for m in _warns(caplog))
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

    def test_a_SPY_config_reaches_the_log_label(self, caplog):
        s = self._build("SPY")
        assert s.underlying_symbol == "SPY"
        assert s.market_data.underlying_symbol == "SPY", (
            "the config value never reached MarketData — this is the shape of "
            "the fix that did nothing")
        with caplog.at_level(logging.WARNING):
            MarketData.update_spx(s.market_data, 684.21, availability="DP")
        assert any("SPY quote is DELAYED" in m for m in _warns(caplog))

    def test_an_SPX_config_is_unchanged(self, caplog):
        s = self._build("SPX")
        assert s.market_data.underlying_symbol == "SPX"
        with caplog.at_level(logging.WARNING):
            MarketData.update_spx(s.market_data, 7176.99, availability="DP")
        assert any("SPX quote is DELAYED" in m for m in _warns(caplog))

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
            self, caplog):
        """The exact bug that shipped and was caught: strategy knows SPY,
        MarketData was never told, log says SPX."""
        md = MarketData()  # built without the labels, as before the fix
        assert md.underlying_symbol == "SPX"
        with caplog.at_level(logging.WARNING):
            MarketData.update_spx(md, 684.21, availability="DP")
        assert any("SPX quote is DELAYED" in m for m in _warns(caplog)), (
            "pre-fix shape: a SPY price reported as a delayed SPX quote")
