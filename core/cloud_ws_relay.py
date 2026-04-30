"""
Cloud WebSocket Relay Client — Fog Node side.

Connects to AWS API Gateway WebSocket as a "fog" client.
When the main app broadcasts a FogRealtimeUpdate, it also calls
send() here to relay the message to all connected Web App clients.

Usage (in app.py):
    self._cloud_ws = CloudWsRelay(settings)
    await self._cloud_ws.start()
    ...
    await self._cloud_ws.send(broadcast.model_dump())
"""
import asyncio
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    logger.warning("websockets package not installed — cloud WS relay disabled.")


class CloudWsRelay:
    """
    Maintains a persistent WebSocket connection to AWS API Gateway WebSocket API.
    Reconnects automatically on disconnect with exponential backoff.
    """

    def __init__(self, ws_url: str, device_id: str = "cushion-01") -> None:
        self._url       = f"{ws_url}?type=fog&device_id={device_id}"
        self._ws        = None
        self._running   = False
        self._task: Optional[asyncio.Task] = None

    # ── Public API ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not _WS_AVAILABLE or not self._url.startswith("wss"):
            logger.info("Cloud WS relay not started (disabled or URL missing).")
            return
        self._running = True
        self._task = asyncio.create_task(self._connection_loop(), name="cloud-ws-relay")
        logger.info("Cloud WS relay starting → %s", self._url)

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()

    async def send(self, data: dict) -> None:
        """Send a dict payload to AWS WebSocket (non-blocking, best-effort)."""
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps(data))
        except Exception as exc:
            logger.debug("Cloud WS send failed: %s", exc)
            self._ws = None  # trigger reconnect on next cycle

    # ── Internal ────────────────────────────────────────────────────────────

    async def _connection_loop(self) -> None:
        backoff = 2
        while self._running:
            try:
                async with websockets.connect(self._url, ping_interval=30) as ws:
                    self._ws = ws
                    backoff  = 2
                    logger.info("Cloud WS relay connected.")
                    # Keep alive — just drain any incoming messages (ignored)
                    async for _ in ws:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Cloud WS relay disconnected: %s. Reconnecting in %ds…", exc, backoff)
            finally:
                self._ws = None

            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

        logger.info("Cloud WS relay stopped.")
