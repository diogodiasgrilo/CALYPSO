"""WebSocket endpoint for dashboard real-time updates."""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from dashboard.backend.auth import SESSION_COOKIE_NAME, dev_mode_open, validate_and_refresh_session

logger = logging.getLogger("dashboard.ws_router")

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
        session = validate_and_refresh_session(websocket.cookies.get(SESSION_COOKIE_NAME))
        if not session:
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
