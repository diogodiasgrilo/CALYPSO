"""Capture the per-leg realized breakdown — the drift is undecomposable without it.

THE PROBLEM. `BROKER-RECONCILE` has logged a drift between IBKR's ledger
`realizedpnl` and ours on all five recorded sessions, and no single convention
explains it:

    date     IBKR        ours gross   drift_gross   ours net    drift_net
    09-18   -138.33      -105.00        +33.33      -137.20      +1.13
    09-21  -3329.45     -2895.00       +434.45     -3088.20    +241.25
    09-22   -156.76      +120.00       +276.76       +53.30    +210.06
    09-23   +127.91      +750.00       +622.09      +612.00    +484.09
    09-24  +4108.43     +3565.00       -543.43     +3331.55    -776.88

"Net of commission" fits ONCE (09-18, $1.13) and the drift then flips sign.

RESOLVED 2026-09-25 — the accumulation question, one of the two unknowns the
message flagged: the figure DOES NOT ACCUMULATE across sessions. It read 0.0 at
03:26 ET the morning after a session that realized $4,108.43; a cumulative
value would still have shown $4,108.43. (An earlier reading of 0.0 on a flat
pre-market account was recorded as "consistent with a daily reset but not
proof" — correctly, since a cumulative counter could read 0.0 for other
reasons. Following a KNOWN non-zero session is what makes it decisive. It does
not pin the exact reset MECHANISM, only that day-to-day comparison is valid.)

STILL OPEN — and why this capture exists: decomposing it needs the per-position
`realizedPnl`, and the daily reset destroys that overnight. It can only be
captured DURING the session that produced the drift. On 09-24 the one leg pair
still reconstructible from an intraday sample (E#4, 7715/7720) came to
IBKR -1,504.58 vs ours -1,470.00 gross / -1,502.20 with commissions — i.e. the
per-position figure looks net-of-commission, which makes IBKR LOWER than our
gross while the aggregate that day was HIGHER. Those do not reconcile, and that
contradiction is the thing to chase with a full breakdown.

Read RAW, not via `_read_open_positions`: that helper drops qty-0 rows, and at
settlement the rows carrying realizedPnl are precisely the closed ones.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bots.hydra.strategy import HydraStrategy  # noqa: E402


def _rig(positions, ledger_realized=4108.43, gross=3565.0, commission=233.45):
    s = HydraStrategy.__new__(HydraStrategy)
    s.dry_run = False
    s.daily_state = SimpleNamespace(
        total_realized_pnl=gross, total_commission=commission)
    s.broker = MagicMock()
    s.broker.get_balance = MagicMock(return_value={
        "raw_ledger": {"USD": {"realizedpnl": ledger_realized,
                               "netliquidationvalue": 1013101.3}}})
    s.broker.get_positions = MagicMock(return_value=positions)
    return s


CLOSED = [
    {"contractDesc": "SPX SEP2026 7715 C", "realizedPnl": -7507.29, "position": 0.0},
    {"contractDesc": "SPX SEP2026 7720 C", "realizedPnl": 6002.71, "position": 0.0},
    {"contractDesc": "SPX SEP2026 7730 C", "realizedPnl": None, "position": -7.0},
    {"contractDesc": "SPX SEP2026 7625 P", "realizedPnl": 0.0, "position": 2.0},
]


class TestPerLegCapture:
    def test_closed_legs_are_captured(self):
        s = _rig(CLOSED)
        out = HydraStrategy._reconcile_pnl_against_broker(s, "2026-09-24")
        legs = out.get("broker_realized_by_leg")
        assert legs, "no per-leg breakdown captured — the drift stays undecomposable"
        assert {l["leg"] for l in legs} == {"SPX SEP2026 7715 C", "SPX SEP2026 7720 C"}

    def test_qty_zero_rows_are_INCLUDED(self):
        """The whole point: at settlement the informative rows are the CLOSED
        ones. Filtering them out (as _read_open_positions does) captures
        nothing."""
        s = _rig(CLOSED)
        out = HydraStrategy._reconcile_pnl_against_broker(s, "2026-09-24")
        assert all(l["qty"] == 0.0 for l in out["broker_realized_by_leg"])

    def test_ordered_by_magnitude(self):
        s = _rig(CLOSED)
        out = HydraStrategy._reconcile_pnl_against_broker(s, "2026-09-24")
        vals = [abs(l["realized"]) for l in out["broker_realized_by_leg"]]
        assert vals == sorted(vals, reverse=True)

    def test_the_aggregate_is_still_reported(self):
        s = _rig(CLOSED)
        out = HydraStrategy._reconcile_pnl_against_broker(s, "2026-09-24")
        assert out["broker_realized_pnl"] == 4108.43
        assert out["drift_vs_gross"] == -543.43


class TestItNeverDisturbsSettlement:
    def test_a_failing_positions_read_does_not_break_the_reconcile(self):
        """CONTROL. This is a diagnostic inside a diagnostic — it must never
        cost us the aggregate, which is the part that actually gets watched."""
        s = _rig(CLOSED)
        s.broker.get_positions = MagicMock(side_effect=RuntimeError("broker down"))
        out = HydraStrategy._reconcile_pnl_against_broker(s, "2026-09-24")
        assert out["drift_vs_gross"] == -543.43
        assert "broker_realized_by_leg" not in out

    def test_no_realized_legs_is_not_an_error(self):
        s = _rig([{"contractDesc": "X", "realizedPnl": 0.0, "position": -7.0}])
        out = HydraStrategy._reconcile_pnl_against_broker(s, "2026-09-24")
        assert "broker_realized_by_leg" not in out
        assert out["broker_realized_pnl"] == 4108.43
