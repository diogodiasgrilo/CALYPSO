"""The broker-contract verifier must EXECUTE in CI, not merely import.

Standing lesson in this repo: if CI runs a script, the local suite must run it
too. Importing proves the file parses; it does not prove `main()` works, and a
verifier that crashes is worse than none — it would be read as "no mismatches".

The script itself is read-only and needs a live broker, so `_rpc` is stubbed
here. What is under test is the VERDICT LOGIC: does it pass a good payload, and
— the part that matters — does it actually FAIL a bad one?
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import verify_broker_contract as V  # noqa: E402


GOOD_POS = [{
    "conid": 1, "position": 0.0, "contractDesc": "SPX SEP2026 7705 C",
    "realizedPnl": -2800.22, "unrealizedPnl": 0.0, "assetClass": "OPT",
    "strike": "7705", "putOrCall": "C", "expiry": "20260924", "avgPrice": 0.0,
}]
GOOD_BAL = {
    "base_currency": "USD",
    "raw_ledger": {"USD": {"realizedpnl": 0.0, "unrealizedpnl": 0.0,
                           "netliquidationvalue": 1013101.3,
                           "cashbalance": 1011055.4}},
}


def _stub(monkeypatch, positions, balance):
    def _rpc(method, args=None, kwargs=None):
        return positions if method == "get_positions" else balance
    monkeypatch.setattr(V, "_rpc", _rpc)


def test_passes_on_the_real_shape(monkeypatch, capsys):
    _stub(monkeypatch, GOOD_POS, GOOD_BAL)
    assert V.main() == 0
    assert "PASS" in capsys.readouterr().out


def test_FAILS_when_a_position_field_disappears(monkeypatch, capsys):
    """CONTROL. This is the whole point — mocks elsewhere would not notice."""
    bad = [dict(GOOD_POS[0])]
    del bad[0]["realizedPnl"]
    _stub(monkeypatch, bad, GOOD_BAL)
    assert V.main() == 1
    assert "realizedPnl" in capsys.readouterr().out


def test_FAILS_when_the_ledger_loses_realizedpnl(monkeypatch, capsys):
    """BROKER-RECONCILE's entire premise is this one field."""
    bal = {"base_currency": "USD", "raw_ledger": {"USD": {"cashbalance": 1.0}}}
    _stub(monkeypatch, GOOD_POS, bal)
    assert V.main() == 1
    assert "realizedpnl" in capsys.readouterr().out


def test_FAILS_if_the_base_currency_is_not_USD(monkeypatch, capsys):
    """B1 deleted a USD->EUR conversion as dead. If the base currency were ever
    NOT USD, that conversion would have been load-bearing after all."""
    bal = dict(GOOD_BAL, base_currency="EUR")
    _stub(monkeypatch, GOOD_POS, bal)
    assert V.main() == 1
    assert "EUR" in capsys.readouterr().out


def test_no_positions_is_reported_but_not_a_failure(monkeypatch, capsys):
    """Pre-market the account is flat; that is not a contract breach."""
    _stub(monkeypatch, [], GOOD_BAL)
    assert V.main() == 0
    assert "cannot verify the position shape" in capsys.readouterr().out
