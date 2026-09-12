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

from bots.hydra.base_strategy import PROGRESSIVE_RETRY_SEQUENCE, MEICDailyState
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
        ):
            assert self.pol.is_retryable(exc), f"{exc!r} should be retryable"
        # A CONTEXT-FREE bare number is intentionally NOT retryable (audit #50):
        # it can't be told apart from a price/size/order-id. Real transient 5xx
        # carry a structured status_code (ExternalBrokerError) or HTTP context
        # ("HTTP 503" / "503 Service Unavailable"), both still retried above.
        assert not self.pol.is_retryable(Exception("503"))
        # 429 is NOT retryable (IBKR-audit #9): retrying triggers IBKR's ~10-min
        # penalty box / permanent-block escalation; _ib_call fail-fasts instead.
        assert not self.pol.is_retryable(Exception("429 Too Many Requests"))

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

    def test_please_query_accounts_is_not_retryable(self):
        # 2026-06-08 fix #5: a 500 'please query /accounts first' (lost session
        # priming after the daily reset) must fail FAST — retrying without a
        # re-prime won't help and would trip the market breaker. The snapshot
        # path's force-reprime self-heal handles recovery.
        assert not self.pol.is_retryable(
            Exception("Bad Request: please query /accounts first")
        )
        assert not self.pol.is_retryable(
            Exception('500 :: {"error":"please query /accounts first"}')
        )


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
    # 2026-08-20: real __init__ always sets these (base_strategy.py:1197 for
    # commission_per_leg, MEICDailyState() for daily_state) before any entry
    # code can run — _flatten_accumulated_partial's round-trip commission
    # booking (execution audit fix) now reads both.
    s.commission_per_leg = 1.15
    s.daily_state = MEICDailyState()
    s._orphaned_orders = []
    s._max_absolute_slippage = 10.0
    s._monitor_fill_slippage = MagicMock()
    s._cancel_order = MagicMock(return_value=True)
    # Default: post-cancel status carries no fill field → _extract returns None,
    # so the fill-the-remainder loop falls back to the pre-cancel snapshot.
    # Tests that exercise the race override this.
    s._get_order_status = MagicMock(return_value={})
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
                     order_type="LMT", limit_price=None, coid=None, **_kw):
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
                     order_type="LMT", limit_price=None, coid=None, **_kw):
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
    """ORDER-010 (fill-the-remainder): a partial fill is COMPLETED by re-placing
    ONLY the still-unfilled remainder up the progressive ladder — NOT aborted
    (the 2026-06-08 forensic's #1 entry-blocker). An accumulated partial is
    flattened only as a last resort (all rungs exhausted) so we never strand a
    naked 0DTE leg, and a clean full fill never cancels."""

    def test_partial_then_remainder_completes_leg(self):
        # Attempt 1 fills 3/10; attempt 2 places the remaining 7 and completes.
        s = _make_strategy()
        calls = []

        def fake_leg(*, instrument_id, side, quantity,
                     order_type="LMT", limit_price=None, coid=None, **_kw):
            calls.append({"side": side, "quantity": quantity,
                          "order_type": order_type, "coid": coid})
            if len(calls) == 1:
                return {"success": True, "filled": False, "filled_quantity": 3,
                        "requested_quantity": quantity, "order_id": "OP",
                        "fill_price": 1.00, "position_id": None, "raw": {}}
            return {"success": True, "filled": True, "filled_quantity": quantity,
                    "requested_quantity": quantity, "order_id": "OR",
                    "fill_price": 1.10, "position_id": None, "raw": {}}

        s._place_leg_order = fake_leg
        # Post-cancel terminal fill == the pre-cancel snapshot (3).
        s._get_order_status = MagicMock(return_value={"filled_quantity": 3})
        with patch("bots.hydra.base_strategy.time.sleep"):
            result = s._place_option_order_ib(
                strike=5000.0, put_call="Call", buy_sell=BuySell.SELL,
                expiry="20260528", external_ref="hydra_20260528_entry1_SC",
            )

        assert result is not None, "leg must complete via the remainder"
        # Two places: the full-target first rung (3 fill), then ONLY the 7 left.
        assert len(calls) == 2, calls
        assert calls[0]["side"] == "SELL" and calls[0]["quantity"] == 10
        assert calls[1]["side"] == "SELL" and calls[1]["quantity"] == 7
        # The partial's working remainder was cancelled before re-placing.
        s._cancel_order.assert_called_once_with("OP")
        # Credit/price reflect the FULL 10 contracts at the blended avg:
        # (1.00*3 + 1.10*7) / 10 = 1.07.
        assert result["fill_price"] == pytest.approx(1.07)
        assert result["credit"] == pytest.approx(1.07 * 100 * 10)

    def test_full_fill_first_attempt_never_cancels(self):
        # A clean full fill on the first rung returns immediately — no cancel,
        # no flatten, no second place.
        s = _make_strategy()
        calls = []

        def fake_leg(*, instrument_id, side, quantity,
                     order_type="LMT", limit_price=None, coid=None, **_kw):
            calls.append(side)
            return {"success": True, "filled": True, "filled_quantity": quantity,
                    "requested_quantity": quantity, "order_id": "OK",
                    "fill_price": 1.05, "position_id": None, "raw": {}}

        s._place_leg_order = fake_leg
        with patch("bots.hydra.base_strategy.time.sleep"):
            result = s._place_option_order_ib(
                strike=5000.0, put_call="Call", buy_sell=BuySell.SELL,
                expiry="20260528", external_ref="hydra_20260528_entry1_SC",
            )
        assert result is not None
        assert len(calls) == 1
        assert result["credit"] == pytest.approx(1.05 * 100 * 10)
        s._cancel_order.assert_not_called()

    def test_partial_never_completes_flattens_on_exhaust(self):
        # Every rung fills 1 and never reaches target → after all rungs, the
        # accumulated partial is flattened ONCE + the leg aborts (return None).
        s = _make_strategy()
        places, flattens = [], []

        def fake_leg(*, instrument_id, side, quantity,
                     order_type="LMT", limit_price=None, coid=None, **_kw):
            if side == "SELL":
                places.append(quantity)
                return {"success": True, "filled": False, "filled_quantity": 1,
                        "requested_quantity": quantity,
                        "order_id": f"OP{len(places)}",
                        "fill_price": 1.0, "position_id": None, "raw": {}}
            flattens.append(quantity)  # the BUY flatten — fills cleanly
            return {"success": True, "filled": True, "filled_quantity": quantity,
                    "requested_quantity": quantity, "order_id": "OF",
                    "fill_price": 1.0, "position_id": None, "raw": {}}

        s._place_leg_order = fake_leg
        s._get_order_status = MagicMock(return_value={"filled_quantity": 1})
        with patch("bots.hydra.base_strategy.time.sleep"):
            result = s._place_option_order_ib(
                strike=5000.0, put_call="Call", buy_sell=BuySell.SELL,
                expiry="20260528", external_ref="hydra_20260528_entry1_SC",
            )

        assert result is None
        # One SELL per rung; a SINGLE flatten of the accumulated partial.
        assert len(places) == len(PROGRESSIVE_RETRY_SEQUENCE)
        assert len(flattens) == 1
        assert flattens[0] == len(places)  # accumulated 1-per-rung total

    def test_failed_flatten_on_exhaust_is_orphaned(self):
        s = _make_strategy()
        s._add_orphaned_order = MagicMock()

        def fake_leg(*, instrument_id, side, quantity,
                     order_type="LMT", limit_price=None, coid=None, **_kw):
            if side == "SELL":
                return {"success": True, "filled": False, "filled_quantity": 1,
                        "requested_quantity": quantity, "order_id": "OP",
                        "fill_price": 1.0, "position_id": None, "raw": {}}
            # The exhaust-flatten FAILS to fill.
            return {"success": False, "filled": False, "filled_quantity": 0,
                    "requested_quantity": quantity, "order_id": None,
                    "fill_price": None, "position_id": None, "raw": {}}

        s._place_leg_order = fake_leg
        s._get_order_status = MagicMock(return_value={"filled_quantity": 1})
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
# M12 — dashboard REST API auth (fastapi-only; skipped where absent).
# 2026-07-31: superseded by real per-account session-cookie auth (was a
# single shared DASHBOARD_API_KEY) — see tests/test_dashboard_auth.py for
# the full login/2FA/lockout/session coverage. This test now pins the same
# no-op-when-no-accounts / enforced-once-an-account-exists contract for
# require_session, the current guard wired into main.py's _api_guard.
# ─────────────────────────────────────────────────────────────────────────
class TestDashboardSessionAuth:
    def test_require_session_enforced_once_an_account_exists(self, tmp_path):
        pytest.importorskip("fastapi")
        import asyncio
        from fastapi import HTTPException
        from dashboard.backend import auth as dash_auth
        from dashboard.backend.config import settings as dash_settings
        from dashboard.backend.services import auth_db, auth_crypto

        original_db = dash_settings.dashboard_auth_db
        try:
            dash_settings.dashboard_auth_db = tmp_path / "auth.db"
            auth_db.init_db(dash_settings.dashboard_auth_db)

            # Disabled when no accounts exist (dev-mode) → no raise.
            asyncio.run(dash_auth.require_session(calypso_session=None))

            # Enforced once an account exists.
            auth_db.create_user(
                dash_settings.dashboard_auth_db, "diogo", auth_crypto.hash_password("x" * 20)
            )
            with pytest.raises(HTTPException):
                asyncio.run(dash_auth.require_session(calypso_session=None))
            with pytest.raises(HTTPException):
                asyncio.run(dash_auth.require_session(calypso_session="not-a-real-token"))
        finally:
            dash_settings.dashboard_auth_db = original_db


class TestOrderWriteDoubleFillGuards:
    """L-C3 + L-H1: the entry-retry / broker-timeout double-fill paths.

    A timed-out place that secretly filled, or a transport timeout that drops
    the order_id, must NEVER lead to a SECOND order under a new cOID on the
    live account.
    """

    def test_transport_timeout_on_entry_place_is_ambiguous(self):
        # L-H1: a broker transport timeout on an ENTRY place → ambiguous (the
        # order may have landed server-side), so the caller aborts not re-places.
        from shared.broker_client import BrokerError
        s = _make_strategy()
        s.broker.place_and_wait_for_fill = MagicMock(
            side_effect=BrokerError(
                "broker unreachable for place_and_wait_for_fill: ReadTimeout: timed out"
            )
        )
        res = s._place_leg_order(
            instrument_id=12345, side="SELL", quantity=10,
            order_type="LMT", limit_price=1.0, coid="c1",
            ambiguous_on_timeout=True,
        )
        assert res.get("ambiguous") is True
        assert res.get("filled") is False

    def test_transport_timeout_on_close_is_clean_failure(self):
        # A CLOSE (ambiguous_on_timeout defaults False) must stay retry-safe.
        from shared.broker_client import BrokerError
        s = _make_strategy()
        s.broker.place_and_wait_for_fill = MagicMock(
            side_effect=BrokerError("broker unreachable for ...: ReadTimeout: timed out")
        )
        res = s._place_leg_order(
            instrument_id=12345, side="BUY", quantity=10, order_type="MKT", coid="c1",
        )
        assert res.get("ambiguous") is None  # NOT ambiguous → caller may retry the close
        assert res.get("success") is False

    def test_broker_rejection_is_not_ambiguous(self):
        # A genuine broker REJECTION (order never landed) must stay retry-safe
        # even on an entry place — only transport-level failures are ambiguous.
        from shared.broker_client import BrokerError
        s = _make_strategy()
        s.broker.place_and_wait_for_fill = MagicMock(
            side_effect=BrokerError(
                "broker place_and_wait_for_fill: MarginError: insufficient margin"
            )
        )
        res = s._place_leg_order(
            instrument_id=12345, side="SELL", quantity=10,
            order_type="LMT", limit_price=1.0, coid="c1",
            ambiguous_on_timeout=True,
        )
        assert res.get("ambiguous") is None
        assert res.get("success") is False

    def test_ambiguous_place_aborts_entry_without_replace(self):
        # L-H1 end-to-end: the progressive loop must NOT advance to a new cOID
        # after an ambiguous place.
        from shared.broker_client import BrokerError
        s = _make_strategy()
        s.broker.place_and_wait_for_fill = MagicMock(
            side_effect=BrokerError("broker unreachable for place: ReadTimeout: timed out")
        )
        with patch("bots.hydra.base_strategy.time.sleep"):
            result = s._place_option_order_ib(
                strike=5000.0, put_call="Call", buy_sell=BuySell.SELL,
                expiry="20260528", external_ref="hydra_20260528_entry1_SC")
        assert result is None
        # Exactly ONE place attempt — the loop aborted, did not re-place.
        assert s.broker.place_and_wait_for_fill.call_count == 1

    def test_lc3_race_fill_completing_leg_is_kept_not_flattened(self):
        # L-C3 / ORDER-010: a place that reports 0 but whose post-cancel TERMINAL
        # status shows the full fill (a fill racing the poll timeout) now
        # COMPLETES the leg — we KEEP a confirmed full fill instead of wastefully
        # flattening it (double commission + slippage) and aborting the whole IC.
        # The cancel returns True (order confirmed no-longer-working), so the
        # count is safe to keep.
        s = _make_strategy()
        place_calls, flatten_calls = [], []

        def fake_leg(*, instrument_id, side, quantity, order_type="LMT",
                     limit_price=None, coid=None, **_kw):
            if side == "SELL":  # the entry place — reports 0 at the timeout
                place_calls.append(coid)
                return {"success": True, "filled": False, "filled_quantity": 0,
                        "requested_quantity": quantity, "order_id": "OENTRY",
                        "fill_price": None, "position_id": None, "raw": {}}
            flatten_calls.append(coid)  # a BUY flatten would mean we wasted it
            return {"success": True, "filled": True, "filled_quantity": quantity,
                    "requested_quantity": quantity, "order_id": "OFLAT",
                    "fill_price": 1.0, "position_id": None, "raw": {}}

        s._place_leg_order = fake_leg
        s._cancel_order = MagicMock(return_value=True)  # cancel confirms dead
        # Post-cancel terminal status shows the entry actually filled in full.
        s._get_order_status = MagicMock(return_value={"filled_quantity": 10})
        with patch("bots.hydra.base_strategy.time.sleep"):
            result = s._place_option_order_ib(
                strike=5000.0, put_call="Call", buy_sell=BuySell.SELL,
                expiry="20260528", external_ref="hydra_20260528_entry1_SC")
        assert result is not None          # the full race-fill is KEPT
        assert len(place_calls) == 1       # ONE entry place — no re-place
        assert len(flatten_calls) == 0     # NOT flattened
        assert result["credit"] > 0

    def test_lc3_failed_cancel_aborts_without_replace(self):
        # L-C3 mode 2: a failed cancel (order may still be working) with no
        # confirmed fill must abort the leg, not advance to a new cOID.
        s = _make_strategy()
        s.alert_service = MagicMock()  # _add_orphaned_order emits a HIGH alert
        s._cancel_order = MagicMock(return_value=False)
        s._get_order_status = MagicMock(return_value={})  # no confirmed fill
        place_calls = []

        def fake_leg(*, instrument_id, side, quantity, order_type="LMT",
                     limit_price=None, coid=None, **_kw):
            place_calls.append(coid)
            return {"success": True, "filled": False, "filled_quantity": 0,
                    "requested_quantity": quantity, "order_id": "OENTRY",
                    "fill_price": None, "position_id": None, "raw": {}}

        s._place_leg_order = fake_leg
        with patch("bots.hydra.base_strategy.time.sleep"):
            result = s._place_option_order_ib(
                strike=5000.0, put_call="Call", buy_sell=BuySell.SELL,
                expiry="20260528", external_ref="hydra_20260528_entry1_SC")
        assert result is None
        assert len(place_calls) == 1   # aborted after first attempt — no re-place
        assert "OENTRY" in s._orphaned_orders
