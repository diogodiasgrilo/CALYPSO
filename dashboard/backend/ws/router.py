"""WebSocket endpoint for dashboard real-time updates."""

import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from dashboard.backend.auth import SESSION_COOKIE_NAME, dev_mode_open, validate_and_refresh_session
from dashboard.backend.config import settings
from dashboard.backend.services import auth_crypto, auth_db

logger = logging.getLogger("dashboard.ws_router")


def _diagnose_ws_auth_failure(raw_cookie) -> str:
    """Explain a WS auth rejection for the log: validate_and_refresh_session()
    itself only returns None, with no visibility into WHY (no cookie sent?
    unknown token? known-but-expired/revoked?). Distinguishing these mattered
    for a real 2026-08-17 incident that looked like a live regression but
    turned out to be a browser tab open since 2026-08-12, retrying forever
    with a session that had simply hit its idle timeout hours earlier — kept
    permanently since a WS auth rejection with no context is otherwise a dead
    end to investigate. Logs only a hash PREFIX, never the raw token (a bearer
    credential) or its full hash."""
    if not raw_cookie:
        return "no_cookie_sent"
    token_hash = auth_crypto.hash_token(raw_cookie)
    try:
        row = auth_db.get_session_raw(settings.dashboard_auth_db, token_hash)
    except Exception as e:
        return f"lookup_error:{e!r}"
    if row is None:
        return f"unknown_token hash_prefix={token_hash[:8]} cookie_len={len(raw_cookie)}"
    expires_at, revoked = row["expires_at"], row["revoked"]
    now = time.time()
    return (
        f"known_token hash_prefix={token_hash[:8]} revoked={bool(revoked)} "
        f"expired={expires_at < now} expires_in_s={round(expires_at - now, 1)}"
    )

router = APIRouter()

# These get set by main.py during app startup
_manager = None
_broadcaster = None


def set_dependencies(manager, broadcaster):
    global _manager, _broadcaster
    _manager = manager
    _broadcaster = broadcaster


@router.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    """Main WebSocket endpoint for dashboard clients.

    Sends full snapshot on connect, then streams deltas.
    """
    # Session-cookie validation (skip if no accounts configured — dev mode,
    # same posture as the REST guard in auth.require_session). The cookie
    # rides along automatically on same-origin WS handshakes — no more
    # secret embedded in the URL.
    if not dev_mode_open():
        raw_cookie = websocket.cookies.get(SESSION_COOKIE_NAME)
        session = validate_and_refresh_session(raw_cookie)
        if not session:
            logger.warning(
                "WS auth rejected (%s): %s",
                websocket.client.host if websocket.client else "?",
                _diagnose_ws_auth_failure(raw_cookie),
            )
            await websocket.close(code=4001, reason="Not authenticated")
            return

    await _manager.connect(websocket)

    try:
        # Send full snapshot on connect — with failure recovery
        try:
            snapshot = await _broadcaster.get_snapshot()
            if not snapshot or not snapshot.get("state"):
                logger.warning("Snapshot has no state data — sending partial snapshot")
            await _manager.send_to(websocket, snapshot)
        except Exception as e:
            logger.error(f"Failed to build snapshot: {e}")
            try:
                await websocket.close(code=1011, reason="Failed to load state")
            except Exception:
                pass
            await _manager.disconnect(websocket)
            return

        # Keep connection alive and handle client messages
        while True:
            data = await websocket.receive_text()
            # Client can send "pong" in response to heartbeat
            # or "refresh" to request a new snapshot
            if data == "refresh":
                try:
                    snapshot = await _broadcaster.get_snapshot()
                    await _manager.send_to(websocket, snapshot)
                except Exception as e:
                    logger.error(f"Failed to build refresh snapshot: {e}")

    except WebSocketDisconnect:
        await _manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
        try:
            await websocket.close(code=1011, reason="Internal error")
        except Exception:
            pass
        await _manager.disconnect(websocket)
