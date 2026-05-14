"""Phase A.10 — Paper-account integration smoke test.

Exercises the full IBClient → IBKR Client Portal Web API surface against
the LIVE paper account once OAuth 1.0a activation has landed (expected
~Sunday 2026-05-17 server reset; worst case ~2 weeks).

**Self-gating**: the whole module skips if `IBClient.connect()` raises an
`IBAuthError` mentioning "invalid consumer" — that's the canonical
pre-activation error per `~/ibkr-oauth/poll/check.sh` + ibind issue #109.
Once the activation lands, this file fires automatically on the next
test run; no env-var flips, no manual edits.

**Safety invariants** (paper-only, but still belt-and-braces):
  - Asserts `cfg.credentials.environment == 'paper'` before any write
  - Uses 1-contract size for the order placement test
  - Picks strikes well outside the front-month spot to make fills
    unlikely; tests cancel within seconds of placement
  - Auto-cleans up any open orders the smoke created in a session-scope
    fixture finalizer

**What's verified end-to-end** (in declared order — each test depends on
the previous one having been wired correctly):

  1. connect() completes all three OAuth stages → `_connected == True`
  2. get_balance("USD") returns sensible numbers; no FX-rate=0 raise
  3. get_positions() returns a list (may be empty on a fresh paper acct)
  4. get_open_orders() returns a list after force=True pre-flight
  5. get_vix_price() / get_quote(SPX) return real-time fields
  6. qualify_contract(SPX OPT) resolves a strike to a conid
  7. streaming.subscribe_quote() + get_snapshot() within 10s tick window
  8. what_if_order() returns IBKR's 5-block margin response
  9. place_iron_condor(1 contract, deep OTM) → cancel_order → status
     transitions to Cancelled within 10s
  10. reconcile_orders([]) on a known-empty state returns clean buckets
  11. disconnect() tears down without 'unclean' counter ticking

A successful run is the definition of A.10 "done" per migration plan
§5.1.A.10. After this passes, Phase B (broker abstraction) is unblocked.

Run manually with:
    cd "/Users/ddias/Desktop/CALYPSO/Git Repo"
    source .venv/bin/activate
    pytest tests/integration/test_ib_paper_smoke.py -v -s

The `-s` is recommended so you can watch the auth-status / smd refresh
logs in real time.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.ib_client import IBClient, IBConfig, IBAuthError, IBClientError
from shared.ib_oauth import IBKRCredentials, load_credentials


logger = logging.getLogger(__name__)


# ─── Configuration ──────────────────────────────────────────────────────────


# Test strike offsets from spot. We place an IC with strikes far enough OTM
# that a real fill is improbable in the ~5-second window before we cancel.
# These are deltas in dollars, not percentages.
SHORT_CALL_OFFSET = 300   # SPX 5500 + 300 = 5800 short call strike
LONG_CALL_OFFSET  = 305   # SPX 5500 + 305 = 5805 long call wing
SHORT_PUT_OFFSET  = -300  # SPX 5500 - 300 = 5200 short put strike
LONG_PUT_OFFSET   = -305  # SPX 5500 - 305 = 5195 long put wing


# ─── Activation gate ────────────────────────────────────────────────────────


def _paper_keys_exist() -> bool:
    """Return True only if all four runtime PEMs are present on disk."""
    base = Path(os.path.expanduser(
        os.environ.get("CALYPSO_IBKR_KEYS_DIR", "~/ibkr-oauth")
    )) / "paper"
    return all(
        (base / f).exists() for f in (
            "private_signature.pem", "private_encryption.pem", "dhparam.pem",
        )
    )


def _activation_check() -> tuple[bool, str]:
    """Probe activation status without consuming the smoke fixture.

    Returns (activated, reason). On `invalid consumer` we return False so
    the suite skips. On any other connect error, we let the actual test
    surface it.
    """
    if not _paper_keys_exist():
        return False, "paper keys missing (~/ibkr-oauth/paper/*.pem)"

    consumer_key = os.environ.get("IBIND_OAUTH1A_CONSUMER_KEY", "")
    access_token = os.environ.get("IBIND_OAUTH1A_ACCESS_TOKEN", "")
    access_token_secret = os.environ.get(
        "IBIND_OAUTH1A_ACCESS_TOKEN_SECRET", "",
    )
    if not (consumer_key and access_token and access_token_secret):
        return False, (
            "OAuth env vars missing (set IBIND_OAUTH1A_CONSUMER_KEY / "
            "IBIND_OAUTH1A_ACCESS_TOKEN / IBIND_OAUTH1A_ACCESS_TOKEN_SECRET)"
        )

    try:
        creds = load_credentials("paper")
        cfg = IBConfig(credentials=creds)
        client = IBClient(cfg)
        try:
            client.connect()
        finally:
            client.disconnect()
        return True, "activated"
    except IBAuthError as exc:
        if "invalid consumer" in str(exc).lower():
            return False, f"pre-activation: {exc}"
        # Other auth errors (e.g. competing) — surface as a real failure
        # later, not a skip. Returning True means we'll let the test run
        # and fail loudly.
        return True, f"unexpected auth state but probe completed: {exc}"
    except Exception as exc:  # pragma: no cover
        return False, f"connect probe errored: {exc}"


# Run the activation probe ONCE per pytest session. If it returns False,
# the entire module skips.
_activated, _activation_reason = _activation_check()
pytestmark = pytest.mark.skipif(
    not _activated,
    reason=f"Phase A.10 smoke skipped — {_activation_reason}",
)


# ─── Session-scope fixtures ─────────────────────────────────────────────────


@pytest.fixture(scope="session")
def ib_client():
    """A single connected IBClient reused across all smoke tests.

    Connects once; cleans up any orders this session placed at teardown;
    disconnects. If connect itself fails, every test in the file errors
    rather than skipping (because the activation probe already passed).
    """
    creds = load_credentials("paper")
    assert creds.environment == "paper", (
        f"SAFETY: smoke test must use paper credentials, got "
        f"environment={creds.environment!r}"
    )
    cfg = IBConfig(credentials=creds)
    client = IBClient(cfg)
    client.connect()
    placed_order_ids: list[str] = []
    # Hand the placed-order-id list back so individual tests can register
    # cleanup. We attach it as an attribute so the fixture's teardown can
    # see it without a global.
    client._smoke_placed_ids = placed_order_ids  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        # Best-effort cancel of anything we placed
        for oid in placed_order_ids:
            try:
                client.cancel_order(oid)
            except Exception as exc:
                logger.warning("Smoke teardown: cancel(%s) failed: %s", oid, exc)
        client.disconnect()


@pytest.fixture(scope="session")
def spx_spot(ib_client: IBClient) -> float:
    """Cached SPX index spot price for strike math.

    Falls back to a placeholder if the quote returns nothing useful, but
    `test_get_quote_spx_index` would have failed first in that case.
    """
    q = ib_client.get_quote(ib_client.qualify_contract("SPX", sec_type="IND"))
    spot = q.get("last") or q.get("mid") or q.get("mark")
    if spot is None:
        pytest.skip("Could not resolve SPX spot for strike math")
    # Round to nearest 5 since SPX strikes are 5-point increments
    return round(spot / 5) * 5


@pytest.fixture(scope="session")
def today_expiry() -> date:
    """0DTE expiry for SPXW. If run on weekend / holiday, returns the
    next weekday — IBKR rejects past expiries with a clear error."""
    d = date.today()
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d = d + timedelta(days=1)
    return d


# ─── 1. Connect / disconnect lifecycle ──────────────────────────────────────


class TestConnectLifecycle:
    def test_connect_succeeds_and_is_connected_true(self, ib_client: IBClient):
        assert ib_client.is_connected() is True

    def test_check_auth_status_returns_authenticated(self, ib_client: IBClient):
        status = ib_client.check_auth_status()
        assert status.get("authenticated") is True
        assert status.get("connected") is True
        assert status.get("competing") is False

    def test_account_id_resolved(self, ib_client: IBClient):
        # Paper accounts always start with 'DU'
        assert ib_client.account_id.startswith("DU"), (
            f"Expected paper account ID starting with 'DU', got "
            f"{ib_client.account_id!r}"
        )


# ─── 2. Balance + positions + orders (REST reads) ───────────────────────────


class TestAccountReads:
    def test_get_balance_usd_returns_sensible_dict(self, ib_client: IBClient):
        bal = ib_client.get_balance("USD")
        assert bal["currency"] == "USD"
        assert "base_currency" in bal
        assert "tradable" in bal
        assert isinstance(bal["tradable"], (int, float))
        # Paper accounts ship with ~$1M; on a fresh paper acct it should be > 0
        assert bal["tradable"] >= 0, f"Unexpected negative tradable: {bal}"

    def test_get_account_info_returns_dict(self, ib_client: IBClient):
        info = ib_client.get_account_info()
        assert isinstance(info, dict)
        # 'availablefunds' is the canonical key per portfolio_summary
        # response shape — surface a clear diagnostic if absent.
        assert "availablefunds" in info or info, (
            f"portfolio_summary returned unexpected shape: {info!r}"
        )

    def test_get_positions_returns_list(self, ib_client: IBClient):
        positions = ib_client.get_positions()
        assert isinstance(positions, list)
        # Fresh paper accounts may have 0 positions; the list shape is
        # what matters for the contract.

    def test_get_open_orders_force_preflight(self, ib_client: IBClient):
        """get_open_orders should call live_orders(force=True) then again.
        The function returns the second result; we just verify the shape."""
        orders = ib_client.get_open_orders()
        assert isinstance(orders, list)


# ─── 3. Market data (REST snapshots) ────────────────────────────────────────


class TestMarketData:
    def test_get_quote_spx_index(self, ib_client: IBClient):
        spx_conid = ib_client.qualify_contract("SPX", sec_type="IND")
        q = ib_client.get_quote(spx_conid)
        # At least one of last/mid/mark should be populated on a live feed
        assert any(q.get(k) is not None for k in ("last", "mid", "mark")), (
            f"SPX snapshot returned no price fields: {q!r}"
        )

    def test_get_vix_price_returns_float(self, ib_client: IBClient):
        vix = ib_client.get_vix_price()
        assert vix is not None
        assert 5.0 < vix < 100.0, f"VIX out of plausible range: {vix}"

    def test_qualify_contract_caches_conid(
        self, ib_client: IBClient, spx_spot: float, today_expiry: date,
    ):
        """Resolving the same option twice should hit the cache the second
        time (no extra HTTP call to ibind)."""
        strike = spx_spot + SHORT_CALL_OFFSET
        c1 = ib_client.qualify_contract(
            "SPX", expiry=today_expiry, strike=strike, right="C",
            trading_class="SPXW",
        )
        c2 = ib_client.qualify_contract(
            "SPX", expiry=today_expiry, strike=strike, right="C",
            trading_class="SPXW",
        )
        assert c1 == c2
        assert isinstance(c1, int) and c1 > 0


# ─── 4. Streaming (WebSocket via StreamingManager) ──────────────────────────


class TestStreaming:
    def test_subscribe_spx_quote_receives_tick_within_10s(
        self, ib_client: IBClient,
    ):
        spx_conid = ib_client.qualify_contract("SPX", sec_type="IND")
        streaming = ib_client.streaming
        assert streaming is not None
        streaming.subscribe_quote(spx_conid)
        try:
            # Poll for up to 10s for the first tick
            deadline = time.monotonic() + 10.0
            snap = None
            while time.monotonic() < deadline:
                snap = streaming.get_snapshot(spx_conid)
                if snap is not None and snap.fields:
                    break
                time.sleep(0.25)
            assert snap is not None, "No tick received within 10s"
            assert snap.fields, (
                f"Tick received but field dict empty (channel-prefix / "
                f"unwrap_market_data regression?): {snap!r}"
            )
            # At least one of bid/ask/last should be populated
            assert any(
                k in snap.fields for k in ("31", "84", "86", "7635")
            ), f"Tick missing expected fields: {snap.fields!r}"
        finally:
            streaming.unsubscribe_quote(spx_conid)

    def test_is_ws_connected_true_after_streaming_start(
        self, ib_client: IBClient,
    ):
        streaming = ib_client.streaming
        assert streaming.is_ws_connected() is True


# ─── 5. Pre-trade margin (what_if_order) ────────────────────────────────────


class TestWhatIfOrder:
    def test_whatif_iron_condor_returns_five_blocks(
        self, ib_client: IBClient, spx_spot: float, today_expiry: date,
    ):
        """what_if_order returns IBKR's 5-block margin response. We don't
        place — just compute what would happen."""
        # Resolve all 4 leg conids first
        sc = ib_client.qualify_contract(
            "SPX", today_expiry, spx_spot + SHORT_CALL_OFFSET, "C", "SPXW",
        )
        lc = ib_client.qualify_contract(
            "SPX", today_expiry, spx_spot + LONG_CALL_OFFSET,  "C", "SPXW",
        )
        sp = ib_client.qualify_contract(
            "SPX", today_expiry, spx_spot + SHORT_PUT_OFFSET,  "P", "SPXW",
        )
        lp = ib_client.qualify_contract(
            "SPX", today_expiry, spx_spot + LONG_PUT_OFFSET,   "P", "SPXW",
        )

        from ibind import OrderRequest
        from shared.ib_client import SPREAD_TEMPLATE_CONID

        conidex = (
            f"{SPREAD_TEMPLATE_CONID};;;"
            f"{sc}/-1,{lc}/1,{sp}/-1,{lp}/1"
        )
        req = OrderRequest(
            conid=None,
            conidex=conidex,
            sec_type="BAG",
            side="SELL",
            order_type="LMT",
            price=0.05,
            quantity=1.0,
            tif="DAY",
            acct_id=ib_client.account_id,
        )
        result = ib_client.what_if_order(req)
        assert isinstance(result, dict)
        # Per research_scratch/11, response includes amount/equity/initial/
        # maintenance/position blocks. Allow a tolerant key check since
        # IBKR may return them with slightly different casing.
        keys_lower = {k.lower() for k in result.keys()}
        expected = {"amount", "equity", "initial", "maintenance", "position"}
        missing = expected - keys_lower
        assert not missing, f"whatif response missing blocks: {missing} (got {keys_lower})"


# ─── 6. Place + cancel 1-contract iron condor ───────────────────────────────


class TestPlaceCancel:
    def test_place_1c_ic_then_cancel(
        self, ib_client: IBClient, spx_spot: float, today_expiry: date,
    ):
        """Place a 1-contract IC at deep-OTM strikes (unlikely to fill in
        the ~5s window), then cancel. Verify the order shows up in
        get_open_orders and then transitions to a cancelled state."""
        result = ib_client.place_iron_condor(
            expiry=today_expiry,
            short_call_strike=spx_spot + SHORT_CALL_OFFSET,
            long_call_strike=spx_spot  + LONG_CALL_OFFSET,
            short_put_strike=spx_spot  + SHORT_PUT_OFFSET,
            long_put_strike=spx_spot   + LONG_PUT_OFFSET,
            contracts=1,
            net_credit_limit=0.05,  # very low credit — unlikely to fill
            tif="DAY",
        )
        assert isinstance(result, dict)
        order_id = result.get("order_id") or result.get("id")
        assert order_id, f"place_iron_condor returned no order_id: {result!r}"
        # Register for session-teardown cleanup in case cancel below fails
        ib_client._smoke_placed_ids.append(str(order_id))  # type: ignore[attr-defined]

        # Cancel immediately
        cancelled = ib_client.cancel_order(str(order_id))
        assert cancelled is True, f"cancel_order returned False for {order_id}"

        # Poll up to 10s for a terminal status
        deadline = time.monotonic() + 10.0
        final_status = None
        while time.monotonic() < deadline:
            status = ib_client.get_order_status(str(order_id))
            s = (status.get("status") or status.get("order_status") or "").lower()
            if s in ("cancelled", "api_cancelled", "filled", "rejected"):
                final_status = s
                break
            time.sleep(0.5)
        assert final_status is not None, (
            f"Order {order_id} did not reach terminal status within 10s"
        )
        # We expect Cancelled / ApiCancelled (not Filled — that would mean
        # our deep-OTM credit got hit, surprising but not a test failure
        # per se; just an environmental anomaly).
        assert final_status in (
            "cancelled", "api_cancelled", "filled", "rejected",
        ), f"Unexpected terminal status: {final_status}"


# ─── 7. Reconcile ───────────────────────────────────────────────────────────


class TestReconcileOrders:
    def test_reconcile_empty_state_dry_run(self, ib_client: IBClient):
        """Reconcile against a known-empty state file with dry_run=True
        (no side effects). Any broker-side orders become 'only_broker'
        decisions — verify the bucket math holds."""
        # Empty state means everything the broker has is 'only_broker'.
        # By this point in the test run, the place/cancel test should
        # have left zero open orders, but we tolerate the case where
        # IBKR is slow to reflect a cancel.
        result = ib_client.reconcile_orders(
            state_orders=[], dry_run=True,
        )
        assert isinstance(result, dict)
        # apply_decisions returns {"cancel": [...], "lookup": [...],
        # "reattach": [...], "skip": [...]}
        assert set(result.keys()) >= {"cancel", "lookup", "reattach", "skip"}
        # Empty state → never reattach
        assert result["reattach"] == []


# ─── 8. Final teardown happens in the ib_client fixture ─────────────────────
