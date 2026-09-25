"""Currency conversion must not run when nothing consumes it.

CAUGHT BY MEASUREMENT, NOT REVIEW. B1 (2026-09-24) guarded the position-snapshot
call site and I reported the dead USD->EUR traffic as eliminated. The first
market-open measurement said otherwise:

    exchangerate:  265 calls / 10 min  (9% of the fleet's IBKR budget)
                   was 376 — so the first fix removed under a third of them

There are FOUR FX call sites in logger_service, all gated on
`self.currency_enabled and <client>`:
    log_trade · log_recovered_positions_full · log_performance_metrics ·
    log_account_summary   <- this one runs every status tick
I had guarded one call site in the strategy and missed all four here. Guarding
sites one at a time is the wrong shape of fix; gating the CAPABILITY kills them
together and cannot drift per-variant.

Every consumer of the converted value builds a Google Sheets COLUMN — `pnl_eur`,
`cumulative_pnl_eur`, `pos_pnl_eur`. Sheets has been off on every variant since
the 2026-07-17 DB migration, and the conversion is a no-op on this account
anyway: the base currency is USD (verified against the live ledger and by
scripts/verify_broker_contract).

So: `currency_enabled` now requires a sink. Re-enabling Sheets restores the old
behaviour exactly — which is what `test_re_enabling_sheets_restores_it` pins.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shared.logger_service import TradeLoggerService  # noqa: E402


def _svc(currency_enabled: bool, sheets_enabled: bool):
    cfg = {
        "currency": {"enabled": currency_enabled,
                     "base_currency": "USD", "account_currency": "EUR"},
        "google_sheets": {"enabled": sheets_enabled},
    }
    svc = TradeLoggerService.__new__(TradeLoggerService)
    # Run only the currency block of __init__ against a stubbed sheets logger.
    with patch("shared.logger_service.GoogleSheetsLogger") as GS, \
         patch("shared.logger_service.LocalFileLogger"), \
         patch("shared.logger_service.MicrosoftSheetsLogger"), \
         patch("shared.logger_service.EmailAlerter"):
        GS.return_value.enabled = sheets_enabled
        TradeLoggerService.__init__(svc, cfg, "TEST")
    return svc


class TestCurrencyRequiresASink:
    def test_suppressed_when_sheets_is_off(self):
        """The live configuration on every variant today."""
        assert _svc(currency_enabled=True, sheets_enabled=False).currency_enabled is False

    def test_re_enabling_sheets_restores_it(self):
        """CONTROL. This is not a deletion — turn the sink back on and the
        conversion comes back. A fix that permanently disabled it would pass
        every other test in this file."""
        assert _svc(currency_enabled=True, sheets_enabled=True).currency_enabled is True

    def test_off_stays_off_when_sheets_is_on(self):
        assert _svc(currency_enabled=False, sheets_enabled=True).currency_enabled is False

    def test_the_request_is_remembered_for_the_log_line(self):
        """Operators need to tell 'suppressed' from 'never configured'."""
        svc = _svc(currency_enabled=True, sheets_enabled=False)
        assert svc._currency_requested is True
        assert svc.currency_enabled is False


class TestNoFxCallWhenSuppressed:
    """Targets `log_performance_metrics` — the site that was ACTUALLY burning the
    calls, established by elimination rather than by assumption.

    Of the four FX sites in this file, `log_account_summary` already returns
    early when Sheets is off, so it was never a source despite running per tick.
    `log_trade` and `log_recovered_positions_full` lack that guard but are
    event-driven. `log_performance_metrics` lacks the guard AND is called every
    status tick from main.py — on all eight variants. That is the ~265 calls per
    10 minutes.

    (An earlier version of this test pointed at `log_account_summary` and passed
    with the bug restored, because of that early return. A test that is green
    either way proves nothing, which is the whole reason the controls below
    exist.)
    """

    def _client(self):
        c = type("C", (), {"calls": 0})()

        def _fx(a, b):
            c.calls += 1
            return 0.92
        c.get_fx_rate = _fx
        return c

    def test_no_fx_round_trip_when_there_is_no_sink(self):
        svc = _svc(currency_enabled=True, sheets_enabled=False)
        c = self._client()
        svc.log_performance_metrics("Intraday", {"total_pnl": 1.0}, saxo_client=c)
        assert c.calls == 0, "still fetching FX with no sink to consume it"

    def test_it_DOES_fetch_when_a_sink_exists(self):
        """CONTROL. Proves the test can tell the two states apart — without
        this, a permanently-disabled conversion would look identical."""
        svc = _svc(currency_enabled=True, sheets_enabled=True)
        c = self._client()
        try:
            svc.log_performance_metrics("Intraday", {"total_pnl": 1.0}, saxo_client=c)
        except Exception:
            pass      # the stubbed sheets sink may fail downstream; the count is the point
        assert c.calls == 1, "the conversion did not happen even with a live sink"
