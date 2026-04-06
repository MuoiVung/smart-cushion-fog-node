"""
WebSocket Monitor for the Fog Node Launcher.

Connects to the Fog Node's WebSocket server as a client and forwards
every broadcast message to the UI as a "fog_to_app" channel message.

This captures exactly what the Web App sees in real time.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class WebSocketMonitor:
    """
    Background WebSocket client that captures Fog→App broadcasts.

    Uses the built-in `websockets` library in a separate thread with
    its own asyncio event loop to avoid conflicting with the tkinter
    main loop.
    """

    def __init__(
        self,
        host:       str = "localhost",
        port:       int = 8765,
        token:      str = "",
        on_message: Optional[Callable] = None,
        on_log:     Optional[Callable[[str], None]] = None,
    ) -> None:
        self._host       = host
        self._port       = port
        self._token      = token
        self._on_message = on_message
        self._on_log     = on_log
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start monitoring in a background thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the background thread to stop."""
        self._stop_event.set()
        self._log("WebSocket monitor stopped")

    def _run(self) -> None:
        """Background thread: keep trying to connect."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._connect_loop())

    async def _connect_loop(self) -> None:
        import asyncio
        try:
            import websockets
        except ImportError:
            self._log("websockets library not available in launcher env")
            return

        url = f"ws://{self._host}:{self._port}"
        backoff = 3

        while not self._stop_event.is_set():
            try:
                headers = {}
                if self._token:
                    headers["Authorization"] = f"Bearer {self._token}"

                async with websockets.connect(url, additional_headers=headers) as ws:
                    self._log(f"WebSocket monitor connected to {url}")
                    async for raw in ws:
                        if self._stop_event.is_set():
                            break
                        self._handle_message(raw)
            except Exception as exc:
                if not self._stop_event.is_set():
                    self._log(f"WebSocket monitor: {exc}, retrying in {backoff}s…")
                    await asyncio.sleep(backoff)

    def _handle_message(self, raw: str) -> None:
        from launcher.mqtt_monitor import MonitorMessage
        try:
            parsed = json.loads(raw)
            # Skip connection handshake messages
            if parsed.get("type") == "connected":
                return
            pretty = json.dumps(parsed, indent=2)
        except json.JSONDecodeError:
            pretty = raw

        msg = MonitorMessage(
            channel="fog_to_app",
            topic="ws://fog:8765",
            payload=pretty,
        )
        if self._on_message:
            self._on_message(msg)

    def _log(self, text: str) -> None:
        logger.info(text)
        if self._on_log:
            self._on_log(text)
