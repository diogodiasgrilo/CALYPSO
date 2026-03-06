"""WebSocket connection manager for broadcasting updates to clients."""

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("dashboard.ws_manager")


class ConnectionManager:
    """Manage WebSocket connections and broadcast messages."""

    def __init__(self):
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info(f"Client connected ({self.client_count} total)")

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.info(f"Client disconnected ({self.client_count} total)")

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a message to all connected clients. Remove dead connections."""
        if not self._connections:
            return

        payload = json.dumps(message)
        dead: list[WebSocket] = []

        async with self._lock:
            connections = list(self._connections)

        for ws in connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)
            logger.info(f"Removed {len(dead)} dead connection(s) ({self.client_count} remaining)")

    async def send_to(self, websocket: WebSocket, message: dict[str, Any]) -> None:
        """Send a message to a specific client."""
        try:
            await websocket.send_text(json.dumps(message))
        except Exception:
            await self.disconnect(websocket)
