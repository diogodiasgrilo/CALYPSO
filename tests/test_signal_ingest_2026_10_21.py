"""signal-ingest: TradingView webhook -> logged trade intent (DRY-RUN ONLY).

Built 2026-10-21 for the InvestAnswers investigation, but deliberately
INDICATOR-AGNOSTIC: whatever ends up feeding it, the job is validate,
de-duplicate, cap, and record. Running it in log-only mode is also how the open
question gets answered — it turns "how many signals per ticker per year?" from
a guess into a table.

The load-bearing properties pinned here:

1. It CANNOT place an order, structurally — it imports no broker client, the
   same invariant `dashboard/` holds. Verified in a subprocess so another
   test's imports cannot make this pass by accident.
2. Every default fails CLOSED. A blank secret and an empty allowlist each
   refuse everything, because a misconfiguration on a path that will one day
   place orders must not degrade to "allow".
3. Rejections and duplicates are recorded too. A ledger holding only admitted
   signals answers the easy question and loses the useful one.
4. A rejected signal may be retried; only an ADMITTED one is deduplicated —
   rejections are often transient (market closed, daily cap).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from services.signal_ingest import config as cfg_mod, gates  # noqa: E402
from services.signal_ingest.ledger import Ledger  # noqa: E402
from services.signal_ingest.main import (  # noqa: E402
    OUTCOME_DUPLICATE, OUTCOME_REJECTED, OUTCOME_WOULD_TRADE, decide)

SECRET = "s3cret-value"
NOW = datetime(2026, 10, 21, 14, 30, tzinfo=timezone.utc)

SYMBOLS = {
    "TSLA": {"asset_class": "equity", "default_quantity": 1, "max_quantity": 5},
    "BTC":  {"asset_class": "crypto", "default_quantity": 0.01, "max_quantity": 0.05},
}


@pytest.fixture
def led(tmp_path):
    return Ledger(str(tmp_path / "signals.db"))


def _cfg(**over):
    c = {"dry_run": True, "shared_secret": SECRET, "symbols": dict(SYMBOLS),
         "max_orders_per_day": 10}
    c.update(over)
    return c


def _sig(**over):
    p = {"secret": SECRET, "ticker": "TSLA", "action": "buy",
         "time": "2026-10-21T14:30:00Z", "source": "TEST"}
    p.update(over)
    return p


def _open():   return True
def _closed(): return False


class TestItCannotTrade:
    """The property that makes a public order-shaped endpoint safe to run."""

    def test_no_broker_client_is_imported(self):
        code = (
            "import sys;"
            "sys.path.insert(0, %r);"
            "import services.signal_ingest.main as m;"
            "bad=[k for k in sys.modules if 'ib_client' in k or 'broker_client' in k"
            " or k.startswith('bots.hydra') or 'ibind' in k];"
            "print(','.join(sorted(bad)))" % ROOT
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, cwd=ROOT, timeout=120)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "", (
            f"signal-ingest imported trading code: {out.stdout.strip()}")

    def test_dry_run_false_is_refused_loudly(self):
        """Not a config flip: there is no live path to enable."""
        with pytest.raises(RuntimeError, match="DRY-RUN ONLY"):
            cfg_mod.assert_dry_run({"dry_run": False})

    def test_every_default_fails_closed(self):
        d = cfg_mod.DEFAULTS
        assert d["dry_run"] is True
        assert d["shared_secret"] == "", "a default secret would be a backdoor"
        assert d["symbols"] == {}, "a default allowlist would admit symbols"
        assert d["host"] == "127.0.0.1", "must not default to a public bind"


class TestAuth:
    def test_blank_configured_secret_refuses_everything(self, led):
        o, r, _ = decide(_sig(), _cfg(shared_secret=""), led, NOW, _open)
        assert o == OUTCOME_REJECTED and "fail-closed" in r

    def test_wrong_secret_rejected(self, led):
        o, r, _ = decide(_sig(secret="nope"), _cfg(), led, NOW, _open)
        assert o == OUTCOME_REJECTED and r == "bad secret"

    def test_missing_secret_rejected(self, led):
        p = _sig(); p.pop("secret")
        o, r, _ = decide(p, _cfg(), led, NOW, _open)
        assert o == OUTCOME_REJECTED and r == "missing secret"

    def test_correct_secret_admits(self, led):
        o, r, q = decide(_sig(), _cfg(), led, NOW, _open)
        assert o == OUTCOME_WOULD_TRADE and r is None and q == 1


class TestShape:
    @pytest.mark.parametrize("over,frag", [
        ({"ticker": ""}, "ticker"),
        ({"ticker": "A" * 20}, "implausible"),
        ({"action": "hodl"}, "action must be"),
        ({"quantity": "abc"}, "non-numeric"),
        ({"quantity": -3}, "must be positive"),
        ({"quantity": 0}, "must be positive"),
    ])
    def test_malformed_payloads_rejected(self, led, over, frag):
        o, r, _ = decide(_sig(**over), _cfg(), led, NOW, _open)
        assert o == OUTCOME_REJECTED and frag in r


class TestAllowlist:
    def test_empty_allowlist_refuses_everything(self, led):
        o, r, _ = decide(_sig(), _cfg(symbols={}), led, NOW, _open)
        assert o == OUTCOME_REJECTED and "fail-closed" in r

    def test_symbol_not_on_list_rejected(self, led):
        o, r, _ = decide(_sig(ticker="GME"), _cfg(), led, NOW, _open)
        assert o == OUTCOME_REJECTED and "not on the symbol allowlist" in r

    def test_allowlist_is_case_insensitive(self, led):
        o, _, _ = decide(_sig(ticker="tsla"), _cfg(), led, NOW, _open)
        assert o == OUTCOME_WOULD_TRADE


class TestQuantity:
    def test_default_quantity_used_when_signal_omits_it(self, led):
        o, _, q = decide(_sig(), _cfg(), led, NOW, _open)
        assert o == OUTCOME_WOULD_TRADE and q == 1

    def test_oversized_quantity_is_CLAMPED_not_rejected(self, led):
        o, r, q = decide(_sig(quantity=999), _cfg(), led, NOW, _open)
        assert o == OUTCOME_WOULD_TRADE and r is None
        assert q == 5, "must clamp to max_quantity, not trade 999"

    def test_no_quantity_and_no_default_is_rejected(self, led):
        cfg = _cfg(symbols={"TSLA": {"asset_class": "equity", "max_quantity": 5}})
        o, r, _ = decide(_sig(), cfg, led, NOW, _open)
        assert o == OUTCOME_REJECTED and "no default_quantity" in r


class TestMarketHours:
    def test_equity_signal_out_of_hours_rejected(self, led):
        o, r, _ = decide(_sig(), _cfg(), led, NOW, _closed)
        assert o == OUTCOME_REJECTED and "market is closed" in r

    def test_crypto_is_exempt_BY_ASSET_CLASS(self, led):
        """24/7 — and the same service handles both, so this must be per-symbol."""
        o, r, q = decide(_sig(ticker="BTC"), _cfg(), led, NOW, _closed)
        assert o == OUTCOME_WOULD_TRADE and r is None and q == 0.01


class TestIdempotency:
    def test_a_resend_is_a_duplicate_not_a_second_order(self, led):
        cfg, sig = _cfg(), _sig()
        o1, _, q1 = decide(sig, cfg, led, NOW, _open)
        led.record(received_at=NOW, key=gates.idempotency_key(sig, NOW),
                   payload=sig, outcome=o1, quantity_effective=q1)
        o2, r2, _ = decide(sig, cfg, led, NOW, _open)
        assert o1 == OUTCOME_WOULD_TRADE
        assert o2 == OUTCOME_DUPLICATE and "resend" in r2

    def test_a_REJECTED_signal_may_be_retried(self, led):
        """Rejections are often transient; treating one as permanent would
        silently swallow the retry."""
        cfg, sig = _cfg(), _sig()
        o1, _, _ = decide(sig, cfg, led, NOW, _closed)          # market closed
        led.record(received_at=NOW, key=gates.idempotency_key(sig, NOW),
                   payload=sig, outcome=o1, reason="closed")
        o2, _, _ = decide(sig, cfg, led, NOW, _open)            # now open
        assert o1 == OUTCOME_REJECTED
        assert o2 == OUTCOME_WOULD_TRADE, "a transient rejection must be retryable"

    def test_distinct_signals_hash_differently(self):
        base = _sig()
        k = gates.idempotency_key(base, NOW)
        for over in ({"ticker": "BTC"}, {"action": "sell"},
                     {"time": "2026-10-21T15:00:00Z"}, {"source": "OTHER"}):
            assert gates.idempotency_key(_sig(**over), NOW) != k, over

    def test_missing_time_falls_back_to_a_MINUTE_bucket(self):
        p = _sig(); p.pop("time")
        same_minute = datetime(2026, 10, 21, 14, 30, 59, tzinfo=timezone.utc)
        next_minute = datetime(2026, 10, 21, 14, 31, 0, tzinfo=timezone.utc)
        assert gates.idempotency_key(p, NOW) == gates.idempotency_key(p, same_minute)
        assert gates.idempotency_key(p, NOW) != gates.idempotency_key(p, next_minute)


class TestDailyCap:
    def test_cap_blocks_further_signals(self, led):
        cfg = _cfg(max_orders_per_day=2)
        for i in range(2):
            s = _sig(time=f"2026-10-21T14:0{i}:00Z")
            o, _, q = decide(s, cfg, led, NOW, _open)
            assert o == OUTCOME_WOULD_TRADE
            led.record(received_at=NOW, key=gates.idempotency_key(s, NOW),
                       payload=s, outcome=o, quantity_effective=q)
        o, r, _ = decide(_sig(time="2026-10-21T14:09:00Z"), cfg, led, NOW, _open)
        assert o == OUTCOME_REJECTED and "daily order cap" in r

    def test_a_RESEND_does_not_consume_cap_budget(self, led):
        """Why dedup is ordered before the cap."""
        cfg, sig = _cfg(max_orders_per_day=1), _sig()
        o, _, q = decide(sig, cfg, led, NOW, _open)
        led.record(received_at=NOW, key=gates.idempotency_key(sig, NOW),
                   payload=sig, outcome=o, quantity_effective=q)
        o2, r2, _ = decide(sig, cfg, led, NOW, _open)
        assert o2 == OUTCOME_DUPLICATE, f"got {o2}/{r2} — cap ran before dedup"


class TestLedger:
    def test_rejections_are_recorded_too(self, led):
        """The ledger is the instrument; it must hold the declines."""
        sig = _sig(ticker="GME")
        o, r, _ = decide(sig, _cfg(), led, NOW, _open)
        led.record(received_at=NOW, key="k1", payload=sig, outcome=o, reason=r)
        import sqlite3
        con = sqlite3.connect(led.path)
        row = con.execute("SELECT outcome, reason, ticker FROM signals").fetchone()
        con.close()
        assert row[0] == OUTCOME_REJECTED and "allowlist" in row[1] and row[2] == "GME"

    def test_the_secret_is_NEVER_persisted(self, led):
        led.record(received_at=NOW, key="k", payload=_sig(), outcome=OUTCOME_WOULD_TRADE)
        import sqlite3
        con = sqlite3.connect(led.path)
        raw = con.execute("SELECT raw FROM signals").fetchone()[0]
        con.close()
        assert SECRET not in raw and json.loads(raw)["secret"] == "***"

    def test_count_today_counts_only_admitted(self, led):
        for outcome in (OUTCOME_WOULD_TRADE, OUTCOME_REJECTED, OUTCOME_DUPLICATE):
            led.record(received_at=NOW, key=f"k{outcome}", payload=_sig(),
                       outcome=outcome)
        assert led.count_today(NOW) == 1


class TestTheHTTPLayer:
    """Drives the real app through the real routing/parsing stack.

    The tests above exercise `decide()`; these prove the webhook endpoint
    itself behaves — including the awkward inputs a public URL will actually
    receive. Uses TestClient rather than a live uvicorn so it runs in CI.
    """

    @pytest.fixture
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from services.signal_ingest.main import create_app
        cfg = _cfg(ledger_path=str(tmp_path / "s.db"))
        app = create_app(cfg, Ledger(str(tmp_path / "s.db")))
        return TestClient(app)

    def test_health_reports_dry_run_and_the_allowlist(self, client):
        d = client.get("/health").json()
        assert d["status"] == "ok" and d["dry_run"] is True
        assert d["symbols"] == ["BTC", "TSLA"] and d["secret_configured"] is True

    def test_health_never_leaks_the_secret(self, client):
        assert SECRET not in client.get("/health").text

    def test_a_crypto_alert_is_admitted(self, client):
        r = client.post("/webhook", json=_sig(ticker="BTC", quantity=0.02))
        assert r.status_code == 200
        assert r.json()["outcome"] == OUTCOME_WOULD_TRADE
        assert r.json()["quantity"] == 0.02 and r.json()["dry_run"] is True

    def test_a_resend_over_HTTP_is_a_duplicate(self, client):
        p = _sig(ticker="BTC")
        assert client.post("/webhook", json=p).json()["outcome"] == OUTCOME_WOULD_TRADE
        assert client.post("/webhook", json=p).json()["outcome"] == OUTCOME_DUPLICATE

    def test_oversized_quantity_is_clamped_over_HTTP(self, client):
        r = client.post("/webhook", json=_sig(ticker="BTC", quantity=99))
        assert r.json()["outcome"] == OUTCOME_WOULD_TRADE and r.json()["quantity"] == 0.05

    def test_bad_secret_rejected_but_still_200(self, client):
        """Non-2xx makes TradingView retry; a rejection that can never succeed
        must not produce a retry storm. The ledger is the record."""
        r = client.post("/webhook", json=_sig(secret="wrong"))
        assert r.status_code == 200 and r.json()["outcome"] == OUTCOME_REJECTED

    def test_a_non_JSON_body_is_recorded_not_crashed(self, client):
        """TradingView alert messages are free text — a misconfigured alert is
        a fact worth recording, not a 500."""
        r = client.post("/webhook", content=b"this is not json",
                        headers={"Content-Type": "text/plain"})
        assert r.status_code == 200 and r.json()["outcome"] == OUTCOME_REJECTED

    def test_an_oversized_body_is_refused(self, client):
        r = client.post("/webhook", content=b"x" * (gates.MAX_PAYLOAD_BYTES + 1),
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 413

    def test_the_ledger_grows_with_every_call_including_rejects(self, client, tmp_path):
        import sqlite3
        client.post("/webhook", json=_sig(ticker="BTC"))
        client.post("/webhook", json=_sig(ticker="GME"))
        client.post("/webhook", json=_sig(secret="wrong"))
        con = sqlite3.connect(str(tmp_path / "s.db"))
        rows = con.execute("SELECT outcome FROM signals ORDER BY id").fetchall()
        con.close()
        assert [r[0] for r in rows] == [
            OUTCOME_WOULD_TRADE, OUTCOME_REJECTED, OUTCOME_REJECTED]
