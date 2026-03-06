"""WebSocket endpoint for dashboard real-time updates."""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from dashboard.backend.config import settings

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
async def websocket_dashboard(
    websocket: WebSocket,
    api_key: str = Query(default=""),
):
    """Main WebSocket endpoint for dashboard clients.

    Sends full snapshot on connect, then streams deltas.
    """
    # API key validation (skip if no key configured)
    if settings.api_key and api_key != settings.api_key:
        await websocket.close(code=4001, reason="Invalid API key")
        return

    await _manager.connect(websocket)

    try:
        # Send full snapshot on connect
        snapshot = await _broadcaster.get_snapshot()
        await _manager.send_to(websocket, snapshot)

        # Keep connection alive and handle client messages
        while True:
            data = await websocket.receive_text()
            # Client can send "pong" in response to heartbeat
            # or "refresh" to request a new snapshot
            if data == "refresh":
                snapshot = await _broadcaster.get_snapshot()
                await _manager.send_to(websocket, snapshot)

    except WebSocketDisconnect:
        await _manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
        await _manager.disconnect(websocket)
