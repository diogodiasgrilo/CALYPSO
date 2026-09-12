"""S2: what_if_naked_margin parsing + summing (strangle margin gate)."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.ib_client import IBClient  # noqa: E402


def _client():
    c = IBClient.__new__(IBClient)
    c._account_id = "DU123"  # account_id is a read-only property
    return c


def test_parse_initial_margin_handles_ibkr_string():
    assert IBClient._parse_whatif_initial_margin(
        {"initial": {"current": "0", "change": "+4,500.00", "after": "+4,500.00"}}
    ) == 4500.0


def test_parse_initial_margin_none_on_missing():
    assert IBClient._parse_whatif_initial_margin({"amount": {}}) is None
    assert IBClient._parse_whatif_initial_margin({}) is None
    assert IBClient._parse_whatif_initial_margin(None) is None


def test_parse_initial_margin_none_on_garbage():
    assert IBClient._parse_whatif_initial_margin({"initial": {"after": "n/a"}}) is None


def test_naked_margin_sums_two_legs():
    c = _client()
    c.what_if_order = MagicMock(return_value={"initial": {"after": "15,625.00"}})
    total = c.what_if_naked_margin([
        {"conid": 1, "side": "SELL", "quantity": 1},
        {"conid": 2, "side": "SELL", "quantity": 1},
    ])
    assert total == 31250.0  # 15,625 × 2 legs (per-leg sum errs high — safe)


def test_naked_margin_none_on_unparseable_block():
    c = _client()
    c.what_if_order = MagicMock(return_value={"initial": {"after": "??"}})
    assert c.what_if_naked_margin([{"conid": 1, "side": "SELL", "quantity": 1}]) is None


def test_naked_margin_none_on_bad_leg():
    c = _client()
    c.what_if_order = MagicMock(return_value={"initial": {"after": "100"}})
    assert c.what_if_naked_margin([{"conid": 1, "side": "HOLD", "quantity": 1}]) is None
    assert c.what_if_naked_margin([]) is None
