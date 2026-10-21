"""Signal-ingest service: receives TradingView webhooks, records trade intents.

DRY-RUN ONLY. See the package docstring: nothing here imports a broker client,
so the service is structurally incapable of placing an order.

Run:  python -m services.signal_ingest.main
Test: python -m scripts.simulate_tradingview_alert --help
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.signal_ingest import __version__, config as cfg_mod, gates  # noqa: E402
from services.signal_ingest.ledger import Ledger  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
logger = logging.getLogger("signal-ingest")

OUTCOME_WOULD_TRADE = "would_trade"
OUTCOME_REJECTED = "rejected"
OUTCOME_DUPLICATE = "duplicate"


def decide(payload: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger,
           now: Optional[datetime] = None,
           is_equity_open=None) -> Tuple[str, Optional[str], Optional[float]]:
    """Run every gate in order. Returns (outcome, reason, effective_quantity).

    ORDER MATTERS and is deliberate: authentication and shape come before
    anything is trusted; identity (dedup) comes before the caps, so a resend
    cannot consume daily-cap budget; market hours come last because it is the
    most transient reason and the one most worth retrying.
    """
    now = now or datetime.now(timezone.utc)

    reason = gates.check_secret(payload.get("secret"), cfg.get("shared_secret"))
    if reason:
        return OUTCOME_REJECTED, reason, None

    reason = gates.check_shape(payload)
    if reason:
        return OUTCOME_REJECTED, reason, None

    ticker = str(payload["ticker"]).upper()
    allowlist = cfg.get("symbols") or {}

    reason = gates.check_allowlist(ticker, allowlist)
    if reason:
        return OUTCOME_REJECTED, reason, None

    key = gates.idempotency_key(payload, now)
    if ledger.seen(key):
        return OUTCOME_DUPLICATE, "already admitted (idempotent resend)", None

    reason, qty = gates.check_quantity(ticker, payload.get("quantity"), allowlist)
    if reason:
        return OUTCOME_REJECTED, reason, None

    reason = gates.check_daily_cap(ledger.count_today(now), cfg.get("max_orders_per_day"))
    if reason:
        return OUTCOME_REJECTED, reason, qty

    reason = gates.check_market_open(ticker, allowlist, now, is_equity_open)
    if reason:
        return OUTCOME_REJECTED, reason, qty

    return OUTCOME_WOULD_TRADE, None, qty


def create_app(cfg: Optional[Dict[str, Any]] = None,
               ledger: Optional[Ledger] = None) -> FastAPI:
    cfg = cfg if cfg is not None else cfg_mod.load()
    cfg_mod.assert_dry_run(cfg)
    ledger = ledger or Ledger(cfg["ledger_path"])

    app = FastAPI(title="CALYPSO signal-ingest", version=__version__)
    app.state.cfg, app.state.ledger = cfg, ledger

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "dry_run": True,          # not read from cfg: this build has no live path
            "symbols": sorted((cfg.get("symbols") or {}).keys()),
            "secret_configured": bool(cfg.get("shared_secret")),
            "signals_today": ledger.count_today(),
        }

    @app.post("/webhook")
    async def webhook(request: Request) -> JSONResponse:
        now = datetime.now(timezone.utc)
        body = await request.body()
        if len(body) > gates.MAX_PAYLOAD_BYTES:
            logger.warning("rejected oversized payload (%d bytes)", len(body))
            return JSONResponse({"outcome": OUTCOME_REJECTED,
                                 "reason": "payload too large"}, status_code=413)
        try:
            import json as _json
            payload = _json.loads(body.decode("utf-8") or "{}")
        except Exception:
            # TradingView alerts are free text; a non-JSON body is a
            # misconfigured alert, which is worth recording rather than 400ing
            # into silence.
            payload = {"_unparsed": body.decode("utf-8", "replace")[:500]}

        if not isinstance(payload, dict):
            payload = {"_unparsed": str(payload)[:500]}

        outcome, reason, qty = decide(payload, app.state.cfg, app.state.ledger, now)
        key = gates.idempotency_key(payload, now)
        app.state.ledger.record(received_at=now, key=key, payload=payload,
                                outcome=outcome, reason=reason,
                                quantity_effective=qty, dry_run=True)

        log = logger.info if outcome == OUTCOME_WOULD_TRADE else logger.warning
        log("%s | %s %s qty=%s | %s", outcome.upper(),
            payload.get("action"), payload.get("ticker"), qty, reason or "admitted")

        # Always 200: TradingView retries non-2xx, and a retry storm against a
        # rejection that will never succeed is pure noise. The ledger is the
        # record, not the status code.
        return JSONResponse({"outcome": outcome, "reason": reason,
                             "quantity": qty, "dry_run": True})

    return app


def main() -> None:
    import uvicorn
    cfg = cfg_mod.load()
    cfg_mod.assert_dry_run(cfg)
    host, port = cfg.get("host", "127.0.0.1"), int(cfg.get("port", 8002))
    if host not in ("127.0.0.1", "localhost"):
        logger.warning("BINDING NON-LOOPBACK (%s) — this endpoint is reachable "
                       "off-box. Ensure nginx auth + IP allowlist are in front.", host)
    logger.info("signal-ingest v%s starting on %s:%s (DRY-RUN, %d symbol(s))",
                __version__, host, port, len(cfg.get("symbols") or {}))
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
