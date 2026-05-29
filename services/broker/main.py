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

from shared.broker_service import BrokerDispatcher, create_app
from shared.ib_client import IBClient, IBConfig
from shared.ib_oauth import load_credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("calypso-broker")

HOST = os.environ.get("CALYPSO_BROKER_HOST", "127.0.0.1")
PORT = int(os.environ.get("CALYPSO_BROKER_PORT", "8788"))
SESSION_CHECK_S = int(os.environ.get("CALYPSO_BROKER_SESSION_CHECK_S", "900"))


def main() -> None:
    logger.info("calypso-broker starting — paper account, single shared session")
    # connect() raises on failure → systemd Restart=always retries (matches the
    # bots' fail-closed behavior; we never serve on a dead session).
    ib = IBClient(IBConfig(credentials=load_credentials("paper")))
    ib.connect()
    logger.info("IBKR session established; starting session-maintenance loop "
                "(every %ss)", SESSION_CHECK_S)

    stop = threading.Event()

    def _maintain() -> None:
        # Centralizes what main.py's per-bot morning + 15-min gates did: the
        # brokerage session does NOT survive IBKR's ~01:00 ET daily reset or the
        # 24h LST TTL. ensure_connected() round-trips auth/status and re-inits a
        # dead session. Because ONLY this process owns the session, there is
        # never a multi-process eviction war.
        while not stop.wait(SESSION_CHECK_S):
            try:
                if not ib.ensure_connected():
                    logger.error("session re-auth FAILED — retrying next cycle")
            except Exception as e:  # noqa: BLE001
                logger.error("ensure_connected error: %s: %s", type(e).__name__, e)

    threading.Thread(target=_maintain, name="broker-session-maintain",
                     daemon=True).start()

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
