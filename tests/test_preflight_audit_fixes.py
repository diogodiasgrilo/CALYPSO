"""Regression tests for the 2026-05-28 pre-cutover audit fix cluster.

Covers the six execution-correctness/durability bugs found by the 20-agent
preflight audit and fixed in the same change set:

  #1  cOID reused across every progressive-slippage retry attempt
  M2  cOID reused across same-day unwind-and-re-enter
  #2  snapshot warmup exits early on the 6509 availability flag
  M4  ...also on the server_id / 6119 routing fields
  #4  is_retryable matched HTTP-code substrings inside conids/ids/notionals
  M3  within-leg partial fill silently dropped (untracked naked leg)
  M11 state write lacked fsync()+temp-cleanup (power-loss corruption)

See docs/migration/PREFLIGHT_AUDIT_FINDINGS.md for the full findings.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.hydra.base_strategy import PROGRESSIVE_RETRY_SEQUENCE
from bots.hydra.order_types import BuySell
from bots.hydra.strategy import HydraStrategy
from shared.ib_client import _SNAPSHOT_METADATA_KEYS, _snapshot_has_data
from shared.ib_retry import RetryPolicy


# ─────────────────────────────────────────────────────────────────────────
# #4 — is_retryable must match HTTP codes as TOKENS, not bare substrings
# ─────────────────────────────────────────────────────────────────────────
class TestIsRetryableTokenMatch:
    def setup_method(self):
        self.pol = RetryPolicy()

    def test_real_http_codes_still_retryable(self):
        for exc in (
            Exception("HTTP 500"), Exception("HTTP 502"),
            Exception("HTTP 503"), Exception("HTTP 504"),
            Exception("503 Service Unavailable"),
            Exception("429 Too Many Requests"),
            Exception("503"),  # bare token — start/end boundaries
        ):
            assert self.pol.is_retryable(exc), f"{exc!r} should be retryable"

    def test_embedded_digit_runs_not_retryable(self):
        """The bug: '503'/'504'/'500' embedded inside a larger number was
        matched as a 5xx and retried. Word boundaries fix that."""
        for exc in (
            Exception("Order 504123 rejected: insufficient buying power"),
            Exception("conid 5031 is not tradable on this account"),
            Exception("strike 5025 outside valid range"),
            Exception("order id 500999 already in terminal state foo"),
        ):
            assert not self.pol.is_retryable(exc), (
                f"{exc!r} embeds a digit run but is NOT an HTTP 5xx — "
                f"must not be retryable"
            )

    def test_permanent_5xx_body_still_short_circuits(self):
        # IBKR's misuse of 503 for a 404-equivalent must stay non-retryable.
        exc = Exception(
            "response error :: 503 :: Service Unavailable :: "
            '{"error":"Order 893931734 is not found","statusCode":503}'
        )
        assert not self.pol.is_retryable(exc)

    def test_transient_network_errors_retryable(self):
        assert self.pol.is_retryable(Exception("connection reset by peer"))
        assert self.pol.is_retryable(Exception("read timed out"))
        assert self.pol.is_retryable(ConnectionError("network unreachable"))

    def test_client_errors_not_retryable(self):
        assert not self.pol.is_retryable(Exception("401 Unauthorized"))
        assert not self.pol.is_retryable(ValueError("bad input"))


# ─────────────────────────────────────────────────────────────────────────
# #2 / M4 — snapshot warmup must treat 6509/server_id/6119 as metadata
# ─────────────────────────────────────────────────────────────────────────
class TestSnapshotWarmupMetadata:
    def test_metadata_set_includes_routing_and_availability(self):
        for k in ("conid", "conidEx", "_updated", "6509", "server_id", "6119"):
            assert k in _SNAPSHOT_METADATA_KEYS

    def test_availability_only_row_is_not_warm(self):
        # conid + _updated + 6509 + server_id + 6119, but NO price field.
        row = [{"conid": 1, "_updated": 2, "6509": "R",
                "server_id": "q0", "6119": "q0"}]
        assert _snapshot_has_data(row) is False

    def test_metadata_only_row_is_not_warm(self):
        assert _snapshot_has_data([{"conid": 1, "conidEx": 1, "_updated": 2}]) is False

    def test_row_with_price_field_is_warm(self):
        # field 31 = last price → real data present.
        assert _snapshot_has_data([{"conid": 1, "_updated": 2, "31": "100.5"}]) is True

    def test_row_with_mark_is_warm(self):
        # field 7635 = mark (VIX's only quote field) → warm.
        assert _snapshot_has_data([{"conid": 1, "6509": "R", "7635": "16.7"}]) is True


# ─────────────────────────────────────────────────────────────────────────
# Shared harness for the order-placement path (#1, M2, M3)
# ─────────────────────────────────────────────────────────────────────────
def _make_strategy():
    s = HydraStrategy.__new__(HydraStrategy)
    s.broker = MagicMock()
    s.dry_run = False
    s.contracts_per_entry = 10
    s._orphaned_orders = []
    s._max_absolute_slippage = 10.0
    s._monitor_fill_slippage = MagicMock()
    s._cancel_order = MagicMock(return_value=True)
    s._validate_order_size = MagicMock(return_value=(True, None))
    # Chain reader: strike 5000 Call → conid 12345
    s._read_option_chain = MagicMock(return_value=({5000.0: 12345}, {}))
    # Tight 2% spread so ORDER-005/006 guards never skip/abort.
    s._read_option_quote = MagicMock(return_value={"bid": 1.00, "ask": 1.02})
    return s


class TestProgressiveRetryCoid:
    """#1 + M2: every distinct order submission must carry a unique cOID."""

    def test_each_attempt_gets_distinct_coid(self):
        s = _make_strategy()
        calls = []

        def fake_leg(*, instrument_id, side, quantity,
                     order_type="LMT", limit_price=None, coid=None):
            calls.append(coid)
            return {"success": True, "filled": False, "filled_quantity": 0,
                    "requested_quantity": quantity, "order_id": f"O{len(calls)}",
                    "fill_price": None, "position_id": None, "raw": {}}

        s._place_leg_order = fake_leg
        with patch("bots.hydra.base_strategy.time.sleep"):
            result = s._place_option_order_ib(
                strike=5000.0, put_call="Call", buy_sell=BuySell.SELL,
                expiry="20260528", external_ref="hydra_20260528_entry1_SC",
            )

        assert result is None  # all attempts missed
        assert len(calls) == len(PROGRESSIVE_RETRY_SEQUENCE)
        assert len(set(calls)) == len(calls), (
            f"cOIDs must be unique per attempt, got {calls}"
        )
        assert all(c.startswith("hydra_20260528_entry1_SC_") for c in calls)

    def test_reentry_same_day_uses_fresh_coids(self):
        """M2: a second placement for the same (day, entry, leg) must not
        collide with the first invocation's cOIDs."""
        s = _make_strategy()
        seen = []

        def fake_leg(*, instrument_id, side, quantity,
                     order_type="LMT", limit_price=None, coid=None):
            seen.append(coid)
            return {"success": True, "filled": False, "filled_quantity": 0,
                    "requested_quantity": quantity, "order_id": "O",
                    "fill_price": None, "position_id": None, "raw": {}}

        s._place_leg_order = fake_leg
        with patch("bots.hydra.base_strategy.time.sleep"):
            s._place_option_order_ib(
                strike=5000.0, put_call="Call", buy_sell=BuySell.SELL,
                expiry="20260528", external_ref="hydra_20260528_entry1_SC")
            first = list(seen)
            seen.clear()
            s._place_option_order_ib(
                strike=5000.0, put_call="Call", buy_sell=BuySell.SELL,
                expiry="20260528", external_ref="hydra_20260528_entry1_SC")
            second = list(seen)

        assert set(first).isdisjoint(set(second)), (
            f"re-entry reused cOIDs: {set(first) & set(second)}"
        )


class TestPartialFillHandling:
    """M3: a partial fill must be flattened, not silently dropped or
    re-attempted at full size (which would overfill)."""

    def test_partial_fill_is_flattened_and_aborts(self):
        s = _make_strategy()
        calls = []

        def fake_leg(*, instrument_id, side, quantity,
                     order_type="LMT", limit_price=None, coid=None):
            calls.append({"side": side, "quantity": quantity,
                          "order_type": order_type, "coid": coid})
            if len(calls) == 1:
                # First attempt: 3 of 10 fill → partial.
                return {"success": True, "filled": False, "filled_quantity": 3,
                        "requested_quantity": quantity, "order_id": "OP",
                        "fill_price": 1.0, "position_id": None, "raw": {}}
            # The flatten order fills fully.
            return {"success": True, "filled": True, "filled_quantity": quantity,
                    "requested_quantity": quantity, "order_id": "OF",
                    "fill_price": 1.0, "position_id": None, "raw": {}}

        s._place_leg_order = fake_leg
        with patch("bots.hydra.base_strategy.time.sleep"):
            result = s._place_option_order_ib(
                strike=5000.0, put_call="Call", buy_sell=BuySell.SELL,
                expiry="20260528", external_ref="hydra_20260528_entry1_SC",
            )

        assert result is None
        # Exactly two legs: the partial entry + the flatten. No 3rd attempt.
        assert len(calls) == 2, f"must not retry after partial, got {calls}"
        # Working remainder cancelled.
        s._cancel_order.assert_called_once_with("OP")
        # Flatten is opposite side (SELL→BUY), only the filled qty, market.
        flat = calls[1]
        assert flat["side"] == "BUY"
        assert flat["quantity"] == 3
        assert flat["order_type"] == "MKT"

    def test_failed_flatten_is_tracked_as_orphan(self):
        s = _make_strategy()
        s._add_orphaned_order = MagicMock()
        calls = []

        def fake_leg(*, instrument_id, side, quantity,
                     order_type="LMT", limit_price=None, coid=None):
            calls.append(coid)
            if len(calls) == 1:
                return {"success": True, "filled": False, "filled_quantity": 3,
                        "requested_quantity": quantity, "order_id": "OP",
                        "fill_price": 1.0, "position_id": None, "raw": {}}
            # Flatten FAILS to fill.
            return {"success": False, "filled": False, "filled_quantity": 0,
                    "requested_quantity": quantity, "order_id": None,
                    "fill_price": None, "position_id": None, "raw": {}}

        s._place_leg_order = fake_leg
        with patch("bots.hydra.base_strategy.time.sleep"):
            result = s._place_option_order_ib(
                strike=5000.0, put_call="Call", buy_sell=BuySell.SELL,
                expiry="20260528", external_ref="hydra_20260528_entry1_SC",
            )
        assert result is None
        s._add_orphaned_order.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────
# M11 — durable, atomic, self-cleaning state write
# ─────────────────────────────────────────────────────────────────────────
class TestAtomicWriteJson:
    def test_writes_valid_json_and_no_temp_left(self, tmp_path):
        s = HydraStrategy.__new__(HydraStrategy)
        p = str(tmp_path / "sub" / "hydra_state.json")
        s._atomic_write_json(p, {"a": 1, "b": [2, 3]})
        assert json.load(open(p)) == {"a": 1, "b": [2, 3]}
        assert not os.path.exists(p + ".tmp")

    def test_fsyncs_file_before_rename(self, tmp_path, monkeypatch):
        s = HydraStrategy.__new__(HydraStrategy)
        seen = {"fsync": 0}
        real_fsync = os.fsync
        monkeypatch.setattr(
            "bots.hydra.strategy.os.fsync",
            lambda fd: (seen.__setitem__("fsync", seen["fsync"] + 1),
                        real_fsync(fd))[1],
        )
        s._atomic_write_json(str(tmp_path / "s.json"), {"x": 1})
        assert seen["fsync"] >= 1, "file data must be fsync'd before rename"

    def test_cleans_temp_on_write_failure(self, tmp_path):
        s = HydraStrategy.__new__(HydraStrategy)
        p = str(tmp_path / "s.json")
        # An object() is not JSON-serializable → json.dump raises mid-write.
        with pytest.raises(TypeError):
            s._atomic_write_json(p, {"bad": object()})
        assert not os.path.exists(p + ".tmp"), "partial temp file must be removed"
        assert not os.path.exists(p), "target must be untouched on failure"


# ─────────────────────────────────────────────────────────────────────────
# #6 — unauthenticated .md path-traversal in the agent-report endpoint
# ─────────────────────────────────────────────────────────────────────────
class TestAgentReportPathTraversal:
    def _reader(self, tmp_path):
        from dashboard.backend.services.agent_reports import AgentReportReader
        intel = tmp_path / "intel"
        (intel / "hermes").mkdir(parents=True)
        (intel / "hermes" / "2026-05-28.md").write_text("daily intel")
        # A sensitive file ABOVE the intel dir that a traversal would target.
        (tmp_path / "secret.md").write_text("TOPSECRET")
        return AgentReportReader(intel)

    def test_valid_report_is_returned(self, tmp_path):
        rep = self._reader(tmp_path).get_report_for_date("hermes", "2026-05-28")
        assert rep is not None and rep["content"] == "daily intel"

    def test_traversal_in_date_blocked(self, tmp_path):
        r = self._reader(tmp_path)
        # Without the fix, glob("../../secret.md") would read TOPSECRET.
        assert r.get_report_for_date("hermes", "../secret") is None
        assert r.get_report_for_date("hermes", "../../secret") is None
        assert r.get_report_for_date("hermes", "2026-05-28*") is None

    def test_traversal_or_unknown_agent_blocked(self, tmp_path):
        r = self._reader(tmp_path)
        assert r.get_report_for_date("../..", "2026-05-28") is None
        assert r.get_latest_report("../../etc") is None
        assert r.get_report_for_date("notanagent", "2026-05-28") is None


# ─────────────────────────────────────────────────────────────────────────
# #5 — Telegram bot token must never reach a log sink
# ─────────────────────────────────────────────────────────────────────────
class TestTelegramTokenRedaction:
    def _filter(self, token):
        from bots.hydra.telegram_commands import _TokenRedactingFilter
        return _TokenRedactingFilter(token)

    def test_token_in_message_is_redacted(self):
        tok = "123456789:AAExampleTokenSecretValue"
        rec = logging.LogRecord(
            "x", logging.ERROR, __file__, 1,
            "Failed to send Telegram message: "
            f"HTTPSConnectionPool(host='api.telegram.org'): /bot{tok}/sendMessage",
            None, None,
        )
        assert self._filter(tok).filter(rec) is True
        assert tok not in rec.getMessage()
        assert "***REDACTED***" in rec.getMessage()

    def test_token_in_args_is_redacted(self):
        tok = "TKN-secret"
        rec = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "err: %s",
            (f"https://api.telegram.org/bot{tok}/getUpdates timed out",), None,
        )
        self._filter(tok).filter(rec)
        assert tok not in rec.getMessage()

    def test_empty_token_is_noop(self):
        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "plain TKN", None, None)
        assert self._filter("").filter(rec) is True
        assert rec.getMessage() == "plain TKN"


# ─────────────────────────────────────────────────────────────────────────
# M12 — dashboard REST API key auth (fastapi-only; skipped where absent)
# ─────────────────────────────────────────────────────────────────────────
class TestDashboardApiKeyAuth:
    def test_require_api_key_enforced_when_configured(self):
        pytest.importorskip("fastapi")
        import asyncio
        from fastapi import HTTPException
        from dashboard.backend import auth as dash_auth
        from dashboard.backend.config import settings as dash_settings

        original = dash_settings.api_key
        try:
            # Disabled when empty → no raise.
            dash_settings.api_key = ""
            asyncio.run(dash_auth.require_api_key(x_api_key=None, api_key=None))

            # Enforced when configured.
            dash_settings.api_key = "sekret"
            with pytest.raises(HTTPException):
                asyncio.run(dash_auth.require_api_key(x_api_key="wrong", api_key=None))
            with pytest.raises(HTTPException):
                asyncio.run(dash_auth.require_api_key(x_api_key=None, api_key=None))
            # Accepts the key via header OR query param.
            asyncio.run(dash_auth.require_api_key(x_api_key="sekret", api_key=None))
            asyncio.run(dash_auth.require_api_key(x_api_key=None, api_key="sekret"))
        finally:
            dash_settings.api_key = original
