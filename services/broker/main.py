"""calypso-broker entrypoint — the single shared IBKR session service.

Owns the ONLY IBClient (one Live Session Token, one ssodh/init, one Tickler,
one daily re-auth loop) and serves the 16-method broker surface on loopback so
HYDRA strategies A/B/C run concurrently without the one-brokerage-session-per-
username eviction war. See docs/migration/BROKER_SESSION_SERVICE_DESIGN.md.

Run:   python -m services.broker.main      (systemd: deploy/calypso-broker.service)

Env:
  CALYPSO_BROKER_HOST            bind host (default 127.0.0.1 — loopback only)
  CALYPSO_BROKER_PORT            bind port (default 8788)
  CALYPSO_BROKER_SESSION_CHECK_S session re-check cadence seconds (default 900)
"""

from __future__ import annotations

import logging
import os
import threading
import time

from bots.hydra.alert_hooks import IBKRAlertHooks
from shared.alert_service import AlertService
from shared.broker_service import BrokerDispatcher, create_app
from shared.ib_client import IBClient, IBConfig
from shared.ib_oauth import load_credentials
from shared.market_hours import get_us_market_time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("calypso-broker")

HOST = os.environ.get("CALYPSO_BROKER_HOST", "127.0.0.1")
PORT = int(os.environ.get("CALYPSO_BROKER_PORT", "8788"))
SESSION_CHECK_S = int(os.environ.get("CALYPSO_BROKER_SESSION_CHECK_S", "900"))
ALERT_POLL_S = int(os.environ.get("CALYPSO_BROKER_ALERT_POLL_S", "10"))


def main() -> None:
    logger.info("calypso-broker starting — paper account, single shared session")
    # connect() raises on failure → systemd Restart=always retries (matches the
    # bots' fail-closed behavior; we never serve on a dead session).
    ib = IBClient(IBConfig(credentials=load_credentials("paper")))
    ib.connect()

    # P5a: breaker + warmup ALERTING lives HERE now (the breakers live in this
    # process's IBClient). The strategies' BrokerClient.circuit_breakers is
    # empty, so this is the single alerter for breaker trips / warmup
    # exhaustion. AlertService publishes to the calypso-alerts Pub/Sub topic —
    # the same delivery path the strategies' alerts use. Minimal config is fine
    # (it only reads config["alerts"], defaulting to enabled).
    alert_hooks = IBKRAlertHooks(ib, AlertService({}, "HYDRA"))
    logger.info("IBKR session established; starting maintenance loop "
                "(alert poll %ss, session re-auth %ss)", ALERT_POLL_S, SESSION_CHECK_S)

    stop = threading.Event()

    def _maintain() -> None:
        # Two cadences in one loop:
        #  • every ALERT_POLL_S: alert_hooks.poll() → breaker-transition + stuck-
        #    open + warmup-exhaustion alerts (catches fast transitions), plus a
        #    daily mark_new_day() on the ET date rollover.
        #  • every SESSION_CHECK_S: ensure_connected() → re-auth (covers IBKR's
        #    ~01:00 ET reset / 24h LST TTL). ONLY this process owns the session,
        #    so there is never a multi-process eviction war.
        last_session_check = time.monotonic()
        last_day = None
        while not stop.wait(ALERT_POLL_S):
            try:
                today = get_us_market_time().date()
                if today != last_day:
                    alert_hooks.mark_new_day()
                    last_day = today
                alert_hooks.poll()
            except Exception as e:  # noqa: BLE001 — alerting must never kill the broker
                logger.error("alert poll error: %s: %s", type(e).__name__, e)
            now = time.monotonic()
            if now - last_session_check >= SESSION_CHECK_S:
                last_session_check = now
                try:
                    if not ib.ensure_connected():
                        logger.error("session re-auth FAILED — retrying next cycle")
                        alert_hooks.on_ensure_connected_failed("broker session re-auth gate")
                except Exception as e:  # noqa: BLE001
                    logger.error("ensure_connected error: %s: %s", type(e).__name__, e)

    threading.Thread(target=_maintain, name="broker-maintain", daemon=True).start()

    app = create_app(BrokerDispatcher(ib))

    @app.on_event("shutdown")
    def _on_shutdown() -> None:  # graceful: stop the loop + release the session
        stop.set()
        try:
            ib.disconnect()
        except Exception:
            pass

    import uvicorn
    logger.info("serving broker RPC on http://%s:%d", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
