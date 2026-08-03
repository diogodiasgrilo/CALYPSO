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
from shared import ib_retry
from shared.alert_service import AlertService
from shared.broker_service import BrokerDispatcher, create_app
from shared.ib_client import IBClient, IBConfig
from shared.ib_oauth import load_credentials
from shared.market_hours import get_us_market_time, is_market_open

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
# Re-auth-failure alert throttle: at most one alert per this many WALL-CLOCK
# seconds (NOT per cycle — SESSION_CHECK_S is 180s, so the old per-4th-cycle
# throttle fired every ~12 min, flooding ~30 HIGH emails over a single Saturday
# on 2026-06-27). Off-hours failures are also suppressed entirely (see below).
REAUTH_ALERT_INTERVAL_S = float(os.environ.get("CALYPSO_BROKER_REAUTH_ALERT_INTERVAL_S", "3600"))


def _should_alert_reauth(consec_failures: int, in_market_hours: bool,
                         now: float, last_alert, interval: float) -> bool:
    """Whether to emit a broker re-auth-failure alert this cycle.

    Alert only when the failure has PERSISTED past the routine reset
    (>= 2 consecutive cycles — the first failure is almost always the benign
    ~01:00 ET IBKR reset), AND it is during market hours (off-hours failures are
    benign: nothing trades and the next daily reset clears them — the 2026-06-27
    weekend flood), AND not more than once per `interval` WALL-CLOCK seconds
    (decoupled from the short SESSION_CHECK_S cycle). `last_alert` is the monotonic
    timestamp of the previous alert this outage, or None if none yet."""
    if consec_failures < 2 or not in_market_hours:
        return False
    return last_alert is None or (now - last_alert) >= interval


def _shutdown_broker(ib: IBClient, stop: threading.Event,
                     maintain_thread: threading.Thread, join_timeout: float = 30) -> None:
    """The broker's graceful-shutdown sequence — extracted from the FastAPI
    shutdown hook (2026-08-03) so it's directly unit-testable rather than only
    exercisable by starting the whole app.

    Findings #36/#67: stop.set() only short-circuits the NEXT loop iteration
    — it cannot interrupt an in-flight ensure_connected() that already entered
    the disconnect()+connect() reconnect. If we called ib.disconnect() now,
    the maintenance thread could finish building a brand-new IbkrClient (fresh
    Tickler + live ssodh/init session) AFTER our teardown, orphaning a live
    IBKR brokerage session and risking the one-session-per-username eviction
    war this service exists to avoid. So join the maintenance thread first
    (bounded, since it's a daemon and connect()/disconnect() are serialized
    under IBClient._call_lock) so no reconnect is in flight, THEN release the
    session — making this final disconnect() authoritative over whatever
    client exists.

    2026-08-03: stop.set() alone still left the join blocking the full 30s
    whenever SIGTERM landed mid-ensure_connected() retry (confirmed incident:
    84 processes SIGKILLed when a deploy restart coincided with the broker
    mid OAuth-rehandshake). ensure_connected() routes through
    IBClient._ib_call(family='session', ...), which is
    abortable_on_shutdown=True by default (every family except 'orders' — see
    shared/ib_retry.py) — setting SHUTDOWN_EVENT here lets any in-flight
    retry backoff abort within one tick instead of completing its ~46s
    schedule, so the join below now resolves in ~1s in the common case
    instead of the full `join_timeout`.
    """
    ib_retry.SHUTDOWN_EVENT.set()
    stop.set()
    maintain_thread.join(timeout=join_timeout)
    if maintain_thread.is_alive():
        logger.warning(
            "broker-maintain thread did not stop within %ss; releasing "
            "session anyway (reconnect may still be in flight)", join_timeout,
        )
    try:
        ib.disconnect()
    except Exception:
        pass


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
        # escalate once it PERSISTS (>=2 cycles), and then ONLY during market
        # hours and at most hourly (wall-clock) — see _should_alert_reauth. The
        # old per-4th-cycle throttle flooded ~30 HIGH emails on a single Saturday
        # (2026-06-27) because SESSION_CHECK_S is 180s → an alert every ~12 min.
        consec_reauth_failures = 0
        last_reauth_alert = None  # monotonic ts of the last re-auth alert this outage
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
                except ib_retry.ShutdownRequested:
                    # 2026-08-03: this is _shutdown_broker deliberately aborting
                    # an in-flight retry (SHUTDOWN_EVENT was just set), NOT a
                    # real broker/IBKR problem — falling through to the
                    # consec_reauth_failures counting below would risk firing a
                    # misleading "session re-auth FAILED" alert on a routine,
                    # successful shutdown (this codebase has fought false
                    # re-auth-alert noise before — see REAUTH_ALERT_INTERVAL_S
                    # above). stop.wait() on the next loop check will exit the
                    # thread momentarily anyway; break now rather than waiting
                    # for that tick.
                    logger.info("session re-auth check aborted — shutdown in progress")
                    break
                except Exception as e:  # noqa: BLE001
                    fail_reason = f"ensure_connected exception: {type(e).__name__}: {e}"
                    logger.error("ensure_connected error: %s: %s", type(e).__name__, e)
                if reauth_ok:
                    if consec_reauth_failures:
                        logger.info("session re-auth recovered after %d failed cycle(s)",
                                    consec_reauth_failures)
                    consec_reauth_failures = 0
                    last_reauth_alert = None  # next outage alerts immediately
                else:
                    consec_reauth_failures += 1
                    if consec_reauth_failures == 1:
                        logger.warning("session re-auth failed (cycle 1) — likely the routine "
                                       "~01:00 ET IBKR reset; retrying next cycle, no alert yet")
                    else:
                        logger.error("session re-auth FAILED x%d consecutive", consec_reauth_failures)
                        try:
                            in_rth = is_market_open()
                        except Exception:  # noqa: BLE001 — unsure → alert (fail toward visibility)
                            in_rth = True
                        if _should_alert_reauth(consec_reauth_failures, in_rth, now,
                                                last_reauth_alert, REAUTH_ALERT_INTERVAL_S):
                            last_reauth_alert = now
                            alert_hooks.on_ensure_connected_failed(fail_reason, will_restart=False)
                        elif not in_rth:
                            logger.info("session re-auth still failing (x%d) but market is "
                                        "closed — suppressing alert (benign off-hours; the "
                                        "next daily reset should clear it)", consec_reauth_failures)

    maintain_thread = threading.Thread(
        target=_maintain, name="broker-maintain", daemon=True
    )
    maintain_thread.start()

    app = create_app(BrokerDispatcher(ib))

    @app.on_event("shutdown")
    def _on_shutdown() -> None:  # graceful: stop the loop + release the session
        _shutdown_broker(ib, stop, maintain_thread)

    import uvicorn
    logger.info("serving broker RPC on http://%s:%d", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
