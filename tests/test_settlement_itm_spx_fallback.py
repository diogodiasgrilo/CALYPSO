"""IBKR-audit #5b — settlement SPX fallback so an ITM-settled short isn't
mis-booked as worthless on a post-close restart.

The 06-17 variant-C bug: E#1's put finished deep ITM (SPX 7420 < short 7430), the
live stop-close FAILED leaving it unbooked, then a post-close RESTART ran settlement
when the live SPX snapshot wasn't warmed up → _settlement_spx_level returned None →
_settlement_booked_pnl assumed worthless → the max-loss put was booked as a
+$336.70 PROFIT. Fix: fall back to the last-known SPX (persisted as last_spx_price,
restored into self.spx_price) before assuming worthless.
"""

from unittest.mock import MagicMock

from bots.hydra.strategy import HydraStrategy
import bots.hydra.base_strategy as base_mod


def _strat(*, live_price, spx_price=0.0, current_price=0.0, raise_read=False):
    s = HydraStrategy.__new__(HydraStrategy)
    s.underlying_symbol = "SPX"
    s.spx_price = spx_price
    s.current_price = current_price
    s.requires_protective_wings = True
    if raise_read:
        s._read_index_price = MagicMock(side_effect=RuntimeError("snapshot cold"))
    else:
        s._read_index_price = MagicMock(return_value=(live_price, True))
    return s


class TestSettlementSpxFallback:
    def test_live_read_used_when_available(self):
        s = _strat(live_price=7420.22, spx_price=7400.0)
        assert s._settlement_spx_level() == 7420.22   # live wins over fallback

    def test_falls_back_to_last_known_when_live_none(self):
        # The exact post-close-restart case: live read returns no price.
        s = _strat(live_price=None, spx_price=7416.5)
        assert s._settlement_spx_level() == 7416.5

    def test_falls_back_when_live_raises(self):
        s = _strat(live_price=None, spx_price=7416.5, raise_read=True)
        assert s._settlement_spx_level() == 7416.5

    def test_falls_back_to_current_price(self):
        s = _strat(live_price=0.0, spx_price=0.0, current_price=7419.0)
        assert s._settlement_spx_level() == 7419.0

    def test_current_price_wins_over_stale_recovered_spx_price(self):
        """2026-08-13: mirrors the same fix in _save_state_to_disk. spx_price
        is a strategy-level attribute set exactly ONCE at process init/state-
        recovery and can be stale by days if the process hasn't restarted
        since; current_price is the last LIVE tick this process actually
        saw. This is the highest-stakes reader of the two (it directly gates
        ITM-vs-worthless settlement booking), so both being nonzero and
        DIFFERING must resolve to current_price, not whichever happened to
        be set first."""
        s = _strat(live_price=None, spx_price=7488.06, current_price=7794.36)
        assert s._settlement_spx_level() == 7794.36

    def test_worthless_only_when_no_reference_at_all(self):
        s = _strat(live_price=None, spx_price=0.0, current_price=0.0)
        assert s._settlement_spx_level() is None   # genuinely unknown → legacy worthless


class TestSettlementBookedPnl:
    def _entry(self, **kw):
        e = base_mod.IronCondorEntry(entry_number=1)
        e.contracts = 7
        for k, v in kw.items():
            setattr(e, k, v)
        return e

    def _strat(self):
        s = HydraStrategy.__new__(HydraStrategy)
        s.requires_protective_wings = True
        return s

    def test_itm_put_books_max_loss_not_worthless(self):
        # The 06-17 C E#1: short put 7430 / long 7425, SPX settles 7420.22, 7c.
        # ITM by 9.78pts → capped at the 5pt width → $3,500 loss; credit kept = $315.
        s = self._strat()
        e = self._entry(put_spread_credit=315.0, short_put_strike=7430.0, long_put_strike=7425.0)
        booked, worthless = s._settlement_booked_pnl(e, "put", 7420.22)
        assert worthless is False
        assert booked == 315.0 - (5 * 100 * 7)        # = -$3,185, NOT +$315
        assert booked < 0

    def test_otm_put_keeps_full_credit(self):
        s = self._strat()
        e = self._entry(put_spread_credit=70.0, short_put_strike=7410.0, long_put_strike=7405.0)
        booked, worthless = s._settlement_booked_pnl(e, "put", 7420.22)   # 7420 > 7410 → OTM
        assert worthless is True
        assert booked == 70.0

    def test_no_settlement_level_assumes_worthless(self):
        s = self._strat()
        e = self._entry(put_spread_credit=315.0, short_put_strike=7430.0, long_put_strike=7425.0)
        booked, worthless = s._settlement_booked_pnl(e, "put", None)
        assert worthless is True
        assert booked == 315.0

    def test_itm_call_books_max_loss(self):
        # Symmetry check: short call 7600 / long 7605, SPX 7620 → ITM 20pts → cap 5pt.
        s = self._strat()
        e = self._entry(call_spread_credit=200.0, short_call_strike=7600.0, long_call_strike=7605.0)
        booked, worthless = s._settlement_booked_pnl(e, "call", 7620.0)
        assert worthless is False
        assert booked == 200.0 - (5 * 100 * 7)
