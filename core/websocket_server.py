"""
WebSocket Server for the Smart Cushion Fog Node.

Streams real-time posture results to connected Web App clients.

Security:
  When WS_AUTH_TOKEN is set in .env, every connecting client must present
  the token in the HTTP Authorization header:
      Authorization: Bearer <token>
  Connections without a valid token are rejected with HTTP 401.
  Leave WS_AUTH_TOKEN empty ONLY for local development on a trusted network.

Broadcast strategy:
  All connected clients receive every posture update. Slow or disconnected
  clients are silently removed; their errors do not block other clients.
"""

import asyncio
import json
import logging
from typing import Set

import websockets
from websockets.server import WebSocketServerProtocol

from config.settings import Settings

logger = logging.getLogger(__name__)


class WebSocketServer:
    """
    Async WebSocket server that broadcasts posture data to all connected clients.

    Usage:
        server = WebSocketServer(settings)
        asyncio.create_task(server.start())   # Non-blocking
        ...
        await server.broadcast({"posture": "correct", ...})
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._clients: Set[WebSocketServerProtocol] = set()

    # ── Public API ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Start listening for WebSocket connections.
        This coroutine runs indefinitely until cancelled.
        """
        auth_note = (
            "token authentication enabled"
            if self._settings.ws_auth_token
            else "WARNING – no authentication (set WS_AUTH_TOKEN for production)"
        )
        logger.info(
            f"WebSocket server listening on "
            f"ws://{self._settings.ws_host}:{self._settings.ws_port} "
            f"({auth_note})"
        )
        async with websockets.serve(
            self._handler,
            self._settings.ws_host,
            self._settings.ws_port,
            ping_interval=20,   # Send a ping every 20 s to detect dead connections
            ping_timeout=10,
        ):
            # Keep the server running until the task is cancelled
            await asyncio.Future()

    async def broadcast(self, payload: dict) -> None:
        """
        Send a JSON payload to all currently connected clients.

        Clients that have disconnected are removed silently.

        Args:
            payload: Dictionary that will be serialised to JSON.
        """
        if not self._clients:
            return

        message = json.dumps(payload, default=str)
        disconnected: Set[WebSocketServerProtocol] = set()

        await asyncio.gather(
            *[self._send_to(client, message, disconnected) for client in self._clients],
            return_exceptions=True,
        )

        # Clean up stale connections
        self._clients -= disconnected

    @property
    def connected_count(self) -> int:
        """Number of currently connected clients."""
        return len(self._clients)

    # ── Private helpers ────────────────────────────────────────────────────

    async def _handler(self, websocket: WebSocketServerProtocol) -> None:
        """
        Handle an incoming WebSocket connection.
        Authenticates, registers the client, then waits for it to disconnect.
        """
        client_address = websocket.remote_address
        logger.info(f"New WebSocket connection from {client_address}")

        # ── Authentication ────────────────────────────────────────────────
        if self._settings.ws_auth_token:
            token = websocket.request.headers.get("Authorization", "")
            expected = f"Bearer {self._settings.ws_auth_token}"
            if token != expected:
                logger.warning(
                    f"WebSocket auth failed from {client_address} – invalid token"
                )
                await websocket.close(code=1008, reason="Invalid or missing auth token")
                return

        self._clients.add(websocket)
        logger.info(
            f"Client {client_address} authenticated. "
            f"Total connected: {len(self._clients)}"
        )

        # Send a welcome message so the client knows it's connected
        try:
            await websocket.send(json.dumps({"type": "connected", "message": "Smart Cushion Fog Node"}))
        except websockets.exceptions.ConnectionClosed:
            pass

        try:
            # Keep connection alive; we only push data, clients don't send anything
            await websocket.wait_closed()
        finally:
            self._clients.discard(websocket)
            logger.info(
                f"Client {client_address} disconnected. "
                f"Total connected: {len(self._clients)}"
            )

    @staticmethod
    async def _send_to(
        client: WebSocketServerProtocol,
        message: str,
        disconnected: Set[WebSocketServerProtocol],
    ) -> None:
        """Send a message to a single client, marking it disconnected on failure."""
        try:
            await client.send(message)
        except (websockets.exceptions.ConnectionClosed, OSError):
            disconnected.add(client)
