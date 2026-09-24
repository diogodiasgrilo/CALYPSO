"""Position snapshots must not spend broker requests when nothing consumes them.

FOUND 2026-09-24 while tracing why B's stop detection runs ~28s behind a breach.
The broker's shared IBKR rate gate (``CALYPSO_IBKR_MAX_RPS=5``) was measured at
**85% saturation** — 2,858 requests in a 600s window — and the mix was:

    52%  iserver/marketdata/snapshot
    32%  portfolio/<acct>/positions/0
    13%  iserver/exchangerate  source=USD target=EUR   <-- 376 calls / 10 min

Those FX calls came from ``HydraStrategy.log_position_snapshot``, which fetched
a USD->EUR rate for the Google Sheets "Positions" tab. Sheets has been disabled
on every variant since the 2026-07-17 DB migration, and ``TradeLoggerService``'s sink
returns on its first line when it is — so the rate (and the whole snapshot) was
computed and thrown away. The account's base currency is USD anyway
(``ib_client.get_balance`` docstring, verified against the live ledger
2026-09-10), so the conversion was a no-op even in principle.

Net effect: 13% of the fleet's entire request budget, spent on a discarded
value, while stop detection queued behind it.

THE CONTROL THAT MATTERS: ``test_fx_IS_fetched_when_sheets_enabled`` — it fails
if the guard is written too broadly (i.e. if it skips unconditionally). Without
it, a guard of ``return`` would keep every other test in this file green. See
the standing lesson that source-presence assertions prove nothing.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.strategy import HydraStrategy  # noqa: E402
from shared.logger_service import TradeLoggerService  # noqa: E402


# --------------------------------------------------------------------------
# TradeLoggerService.position_snapshot_enabled
# --------------------------------------------------------------------------
class TestPositionSnapshotEnabledPredicate:
    def _logger(self, google_logger):
        tl = TradeLoggerService.__new__(TradeLoggerService)  # bypass the heavy __init__
        tl.google_logger = google_logger
        return tl

    def test_false_when_sheets_disabled(self):
        assert self._logger(SimpleNamespace(enabled=False)).position_snapshot_enabled is False

    def test_true_when_sheets_enabled(self):
        assert self._logger(SimpleNamespace(enabled=True)).position_snapshot_enabled is True

    def test_fails_open_when_flag_unreadable(self):
        """An unreadable flag must NOT silently switch off a live sink."""
        class Exploding:
            @property
            def enabled(self):
                raise RuntimeError("sheets client half-built")

        assert self._logger(Exploding()).position_snapshot_enabled is True

    def test_fails_open_when_attribute_missing(self):
        assert self._logger(SimpleNamespace()).position_snapshot_enabled is True


# --------------------------------------------------------------------------
# HydraStrategy.log_position_snapshot
# --------------------------------------------------------------------------
def _stub(*, snapshot_enabled: bool):
    """Minimal stand-in carrying only what log_position_snapshot touches.

    ``daily_state.entries`` is empty so the per-entry loop is skipped and the
    method runs straight through to the sink — which is what lets the enabled
    case assert the body completed rather than died in the swallowing
    try/except.
    """
    trade_logger = SimpleNamespace(
        position_snapshot_enabled=snapshot_enabled,
        currency_enabled=True,
        base_currency="USD",
        account_currency="EUR",
        log_position_snapshot=MagicMock(),
    )
    return SimpleNamespace(
        trade_logger=trade_logger,
        daily_state=SimpleNamespace(entries=[]),
        _read_fx_rate=MagicMock(return_value=0.92),
    )

def test_no_broker_traffic_when_sheets_disabled():
    """The whole point: zero IBKR calls, zero sink calls."""
    s = _stub(snapshot_enabled=False)
    HydraStrategy.log_position_snapshot(s)
    s._read_fx_rate.assert_not_called()
    s.trade_logger.log_position_snapshot.assert_not_called()


def test_fx_IS_fetched_when_sheets_enabled():
    """CONTROL. An over-broad guard (unconditional ``return``) turns this red.

    Also asserts the sink was reached, which proves the method body actually
    ran to completion instead of being swallowed by its own try/except.
    """
    s = _stub(snapshot_enabled=True)
    with patch("bots.hydra.strategy.logger") as log:
        HydraStrategy.log_position_snapshot(s)
        assert not log.error.called, f"body raised and was swallowed: {log.error.call_args}"
    s._read_fx_rate.assert_called_once_with("USD", "EUR")
    s.trade_logger.log_position_snapshot.assert_called_once()


def test_force_does_not_bypass_the_guard():
    """Settlement passes force=True; it must not resurrect the dead FX call."""
    s = _stub(snapshot_enabled=False)
    HydraStrategy.log_position_snapshot(s, force=True)
    s._read_fx_rate.assert_not_called()
    s.trade_logger.log_position_snapshot.assert_not_called()
