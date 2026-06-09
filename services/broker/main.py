"""calypso-broker entrypoint — the single shared IBKR session service.

Owns the ONLY IBClient (one Live Session Token, one ssodh/init, one Tickler,
one session re-check / re-auth loop running every CALYPSO_BROKER_SESSION_CHECK_S
seconds, default 15 min) and serves the 16-method broker surface on loopback so
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
import logging.handlers
import os
import threading
import time
from datetime import datetime

from bots.hydra.alert_hooks import IBKRAlertHooks
from shared.alert_service import AlertService
from shared.broker_service import BrokerDispatcher, create_app
from shared.ib_client import IBClient, IBConfig
from shared.ib_oauth import load_credentials
from shared.market_hours import get_us_market_time

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None

# Render asctime in US/Eastern so broker.log lines line up with the bots'
# ET-stamped logs and the entry-window watchdog's ET time-window filter.
# NB: logging calls Formatter.converter as an unbound class attribute — a plain
# function/lambda assigned here would bind `self` and be called with 2 args
# (TypeError → every format() fails → no log output), so wrap in staticmethod.
def _to_et(secs):
    return datetime.fromtimestamp(secs, _ET).timetuple()

if _ET is not None:
    logging.Formatter.converter = staticmethod(_to_et)

# The broker previously logged via a bare basicConfig() → stderr, which never
# reached journald (an imported module configures root logging first, making a
# second basicConfig() a no-op) and left NO persistent log to post-mortem from.
# Fix: own handlers + force=True so OURS win, plus a rotating file at a known
# path. Falls back to stderr-only on a dev box without /opt/calypso.
_LOG_PATH = os.environ.get("CALYPSO_BROKER_LOG", "/opt/calypso/logs/broker/broker.log")
_handlers = [logging.StreamHandler()]
try:
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    _handlers.append(
        logging.handlers.RotatingFileHandler(
            _LOG_PATH, maxBytes=10_000_000, backupCount=7, encoding="utf-8"
        )
    )
except OSError:
    pass
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=_handlers,
    force=True,
)
logger = logging.getLogger("calypso-broker")

HOST = os.environ.get("CALYPSO_BROKER_HOST", "127.0.0.1")
PORT = int(os.environ.get("CALYPSO_BROKER_PORT", "8788"))
# I-M3: default 180s (was 900s). A competing/lost IBKR session was recovering in
# up to 900s — a long availability gap for the shared-session SPOF. 180s tightens
# recovery while staying well clear of the rate ceiling. Override via env.
SESSION_CHECK_S = int(os.environ.get("CALYPSO_BROKER_SESSION_CHECK_S", "180"))
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
        # Consecutive re-auth failures. The routine ~01:00 ET IBKR reset (and
        # brief blips) clear in a SINGLE cycle, so the first failure must NOT
        # alert — that was a nightly false HIGH ("IBKR session lost"). We only
        # escalate once it PERSISTS (≥2 cycles ≈ 30+ min), then re-remind every
        # ~4th cycle so a genuine RTH outage keeps nagging without spamming.
        consec_reauth_failures = 0
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
                reauth_ok = False
                fail_reason = "broker session re-auth gate"
                try:
                    reauth_ok = ib.ensure_connected()
                except Exception as e:  # noqa: BLE001
                    fail_reason = f"ensure_connected exception: {type(e).__name__}: {e}"
                    logger.error("ensure_connected error: %s: %s", type(e).__name__, e)
                if reauth_ok:
                    if consec_reauth_failures:
                        logger.info("session re-auth recovered after %d failed cycle(s)",
                                    consec_reauth_failures)
                    consec_reauth_failures = 0
                else:
                    consec_reauth_failures += 1
                    if consec_reauth_failures == 1:
                        logger.warning("session re-auth failed (cycle 1) — likely the routine "
                                       "~01:00 ET IBKR reset; retrying next cycle, no alert yet")
                    else:
                        logger.error("session re-auth FAILED x%d consecutive", consec_reauth_failures)
                        if consec_reauth_failures % 4 == 2:  # cycles 2, 6, 10, …
                            alert_hooks.on_ensure_connected_failed(fail_reason, will_restart=False)

    maintain_thread = threading.Thread(
        target=_maintain, name="broker-maintain", daemon=True
    )
    maintain_thread.start()

    app = create_app(BrokerDispatcher(ib))

    @app.on_event("shutdown")
    def _on_shutdown() -> None:  # graceful: stop the loop + release the session
        # Findings #36/#67: stop.set() only short-circuits the NEXT loop
        # iteration — it cannot interrupt an in-flight ensure_connected() that
        # already entered the disconnect()+connect() reconnect. If we called
        # ib.disconnect() now, the maintenance thread could finish building a
        # brand-new IbkrClient (fresh Tickler + live ssodh/init session) AFTER
        # our teardown, orphaning a live IBKR brokerage session and risking the
        # one-session-per-username eviction war this service exists to avoid.
        # So join the maintenance thread first (bounded, since it's a daemon and
        # connect()/disconnect() are serialized under IBClient._call_lock) so no
        # reconnect is in flight, THEN release the session — making this final
        # disconnect() authoritative over whatever client exists.
        stop.set()
        maintain_thread.join(timeout=30)
        if maintain_thread.is_alive():
            logger.warning(
                "broker-maintain thread did not stop within 30s; releasing "
                "session anyway (reconnect may still be in flight)"
            )
        try:
            ib.disconnect()
        except Exception:
            pass

    import uvicorn
    logger.info("serving broker RPC on http://%s:%d", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
