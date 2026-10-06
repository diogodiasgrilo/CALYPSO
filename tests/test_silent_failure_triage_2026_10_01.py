"""Three silent failures that could change a decision, not just a record.

An audit of the money path found 35 exception handlers logging at DEBUG, which
the bot never emits at INFO. Most are correctly debug — shadow entries, greeks,
heartbeats, hedge recording: telemetry whose loss costs a row, not a decision.

These three are different, and only these three were raised:

  * the price refresh in `_check_stop_losses` — take-profit, the GEX breach
    exit and the credit+buffer stop ALL read the spread values it updates, so a
    silent failure means every one of them decides on stale prices. Right after
    placement the dataclass default is 0.0, which take-profit reads as 100%
    captured.
  * the MKT-047 escalation check — a silent skip falls back to LIMIT near the
    expiry, so a leg may not flatten, which is the outcome MKT-047 exists to
    prevent.
  * an unparseable IB position — this list feeds reconciliation and naked-short
    detection, so a leg we cannot parse is a leg the bot believes it does not
    hold.

`place_and_wait_for_fill`'s final-status poll was deliberately LEFT at debug:
it degrades to a defined `timed_out` path whose caller logs its own
cancel-then-verify escalation, and it lives in `shared/ib_client.py`, which
would need a broker restart for a medium-strength case.
"""
from __future__ import annotations

import inspect
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _handler(fn, marker):
    """The except-block AROUND `marker`, not everything after it.

    First version took 1400 chars FORWARD from the marker, which runs past the
    handler into unrelated code — and those later lines contain legitimate
    `logger.debug` calls, so two tests failed on code they were never meant to
    inspect. Walk BACK to the enclosing `except` and stop at the next
    dedent-level statement.
    """
    src = inspect.getsource(fn).splitlines()
    hit = next((i for i, l in enumerate(src) if marker in l), None)
    assert hit is not None, f"marker {marker!r} not found in {fn.__name__}"
    start = next((i for i in range(hit, -1, -1)
                  if re.match(r"\s*except\b", src[i])), None)
    assert start is not None, f"no enclosing except for {marker!r}"
    indent = len(src[start]) - len(src[start].lstrip())
    end = hit + 1
    while end < len(src):
        l = src[end]
        if l.strip() and (len(l) - len(l.lstrip())) <= indent:
            break
        end += 1
    return "\n".join(src[start:end])


def _no_debug_call(text):
    return [l for l in text.splitlines()
            if "logger.debug(" in l and not l.strip().startswith("#")]


class TestStalePricesAreLoud:
    def test_the_price_refresh_failure_warns(self):
        from bots.hydra.brandon.strategy import BrandonHydraStrategy as B
        tail = _handler(B._check_stop_losses, "price refresh FAILED")
        assert not _no_debug_call(tail), (
            "the price-refresh failure is back at DEBUG — take-profit and stop "
            "checks would silently decide on stale spread values")
        assert "logger.warning" in tail

    def test_it_says_the_decisions_are_affected(self):
        """'non-fatal' was the original wording and it is misleading: the tick's
        TP and stop decisions ARE affected."""
        from bots.hydra.brandon.strategy import BrandonHydraStrategy as B
        tail = _handler(B._check_stop_losses, "price refresh FAILED")
        assert "STALE" in tail


class TestTheFlattenFallbackIsLoud:
    def test_mkt047_escalation_failure_warns(self):
        from bots.hydra.base_strategy import MEICStrategy as M
        tail = _handler(M._place_marketable_close, "MKT-047 escalation check")
        assert not _no_debug_call(tail), (
            "a skipped MKT-047 escalation is back at DEBUG — a leg could ride "
            "into settlement unflattened and nobody would see it")
        assert "logger.warning" in tail

    def test_it_says_the_leg_may_not_flatten(self):
        from bots.hydra.base_strategy import MEICStrategy as M
        tail = _handler(M._place_marketable_close, "MKT-047 escalation check")
        assert "may not flatten" in tail


class TestAnInvisiblePositionIsLoud:
    def test_unparseable_position_warns(self):
        from bots.hydra.strategy import HydraStrategy as H
        tail = _handler(H._read_open_positions, "unparseable")
        assert not _no_debug_call(tail), (
            "a skipped position is back at DEBUG — it is invisible to "
            "reconciliation and naked-short detection")
        assert "logger.warning" in tail

    def test_it_names_what_goes_blind(self):
        from bots.hydra.strategy import HydraStrategy as H
        tail = _handler(H._read_open_positions, "unparseable")
        assert "naked-short" in tail and "reconciliation" in tail


class TestTheTriageWasNarrow:
    """Only the three were raised. A blanket sweep of all 35 would be a large
    money-path diff for marginal benefit, and would bury the real signals in
    noise from telemetry that is correctly quiet."""

    def test_routine_telemetry_is_still_debug(self):
        """These must not be raised above DEBUG — losing one costs a row, not a
        decision, and WARNING here is noise that trains people to ignore the
        real ones.

        2026-10-06: this asserted `"logger.debug" in src`, which broke when the
        handlers moved to `_swallow.note(site, exc, logger, msg)` — a helper
        that counts the swallow AND still logs at debug. Behaviour unchanged,
        grep broken: the test was pinning the spelling rather than the level.
        Now it asserts what actually matters — that nothing here logs ABOVE
        debug — so a future refactor of the call shape cannot fail it falsely,
        while raising one to WARNING still does.
        """
        from bots.hydra.strategy import HydraStrategy as H
        for fn in (H._record_heartbeat_to_db, H._record_shadow_entry):
            src = inspect.getsource(fn)
            assert ("logger.debug" in src or "_swallow.note" in src), (
                f"{fn.__name__} no longer logs its swallow at all")
            for loud in ("logger.warning", "logger.error", "logger.critical"):
                assert loud not in src, (
                    f"{fn.__name__} was raised to {loud} — see the docstring")

    def test_the_order_poll_was_deliberately_left(self):
        """Documented decision, not an oversight: it degrades to a defined path
        and lives in a file that needs a broker restart."""
        import io as _io
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = _io.open(os.path.join(root, "shared/ib_client.py"),
                       encoding="utf-8").read()
        i = src.find("final status poll for")
        assert i > 0, "the handler moved; re-triage it"
        assert "logger.debug" in src[max(0, i - 300):i + 200]
