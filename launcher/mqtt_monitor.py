"""
MQTT Monitor for the Fog Node Launcher.

Subscribes to all relevant MQTT topics and routes incoming messages
to named queues so the UI thread can consume them safely.

Topics monitored:
  cushion/raw      → ESP32 → Fog   (incoming sensor data)
  cushion/control  → Fog → ESP32   (outgoing vibration commands)
  cushion/sync     → Fog → Cloud   (cloud sync summaries)

The "Fog → App" channel is monitored separately by connecting as a
WebSocket client to the Fog Node's WebSocket server.
"""

import json
import logging
import queue
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


@dataclass
class MonitorMessage:
    """A single captured MQTT message."""
    channel:   str       # "esp32_to_fog" | "fog_to_esp32" | "fog_to_cloud"
    topic:     str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))
    payload:   str = ""


class MQTTMonitor:
    """
    Passive MQTT listener that captures all message flows.

    Connects to the local Mosquitto broker as a read-only observer and
    puts captured messages into a thread-safe queue for the UI to consume.

    Usage:
        monitor = MQTTMonitor(settings, on_message=my_queue.put)
        monitor.start()
        ...
        monitor.stop()
    """

    # Topic → channel name mapping for the UI
    TOPIC_CHANNEL_MAP = {
        "cushion/raw":     "esp32_to_fog",
        "cushion/control": "fog_to_esp32",
        "cushion/sync":    "fog_to_cloud",
    }

    def __init__(
        self,
        host:         str = "localhost",
        port:         int = 1883,
        username:     str = "",
        password:     str = "",
        on_message:   Optional[Callable[[MonitorMessage], None]] = None,
        on_log:       Optional[Callable[[str], None]]            = None,
    ) -> None:
        self._host       = host
        self._port       = port
        self._username   = username
        self._password   = password
        self._on_message = on_message
        self._on_log     = on_log
        self._client: Optional[mqtt.Client] = None
        self._connected  = False
        self._retry_stop = threading.Event()

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the monitor in a background thread (non-blocking)."""
        self._retry_stop.clear()
        thread = threading.Thread(target=self._connect_loop, daemon=True)
        thread.start()

    def stop(self) -> None:
        """Disconnect and stop the monitor."""
        self._retry_stop.set()
        if self._client and self._connected:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False
        self._log("MQTT monitor stopped")

    def publish_config(self, key: str, value: any) -> None:
        """Publish a configuration update to the Fog Node."""
        if self._client and self._connected:
            payload = json.dumps({key: value})
            self._client.publish("cushion/fog/config", payload, qos=1)
            self._log(f"Published config update: {key}={value}")
        else:
            self._log(f"⚠️ Cannot publish config: MQTT monitor not connected")

    def publish_model_reload(self, model_type: str, model_path: str, scaler_path: str) -> bool:
        """
        Publish a hot-reload command to the Fog Node.

        The Fog Node will swap its InferenceEngine instance in-place without
        restarting Docker or the Python process.

        Returns:
            True if the message was published, False if MQTT is not connected.
        """
        if self._client and self._connected:
            from pathlib import Path
            payload = json.dumps({
                "model_type":  model_type,
                "model_path":  model_path,
                "scaler_path": scaler_path,
            })
            self._client.publish("cushion/fog/model_reload", payload, qos=1)
            self._log(f"🔥 Hot-reload published: {Path(model_path).name} + {Path(scaler_path).name}")
            return True
        else:
            self._log("⚠️ Cannot publish model_reload: MQTT monitor not connected")
            return False

    # ── Connection loop ────────────────────────────────────────────────────

    def _connect_loop(self) -> None:
        """Retry connection every 3 seconds until successful or stopped."""
        backoff = 3
        while not self._retry_stop.is_set():
            try:
                self._log("MQTT monitor: connecting…")
                self._build_client()
                self._client.connect(self._host, self._port, keepalive=30)
                self._client.loop_start()
                # Wait until stopped
                self._retry_stop.wait()
                return
            except Exception as exc:
                self._log(f"MQTT monitor: connection failed ({exc}), retrying in {backoff}s…")
                time.sleep(backoff)

    def _build_client(self) -> None:
        """Build and configure a new paho client."""
        self._client = mqtt.Client(
            client_id=f"fog-launcher-monitor-{int(time.time())}",
            protocol=mqtt.MQTTv311,
        )
        if self._username:
            self._client.username_pw_set(self._username, self._password)

        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_mqtt_message

    # ── paho callbacks ─────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            self._connected = True
            self._log("MQTT monitor connected")
            for topic in self.TOPIC_CHANNEL_MAP:
                client.subscribe(topic, qos=0)
                self._log(f"  Subscribed to: {topic}")
        else:
            self._log(f"MQTT monitor: connection refused (rc={rc})")

    def _on_disconnect(self, client, userdata, rc) -> None:
        self._connected = False
        if rc != 0:
            self._log(f"MQTT monitor: disconnected unexpectedly (rc={rc})")

    def _on_mqtt_message(self, client, userdata, message: mqtt.MQTTMessage) -> None:
        topic    = message.topic
        channel  = self.TOPIC_CHANNEL_MAP.get(topic, "unknown")
        raw      = message.payload.decode("utf-8", errors="replace")

        # Pretty-print JSON payloads
        try:
            parsed = json.loads(raw)
            pretty = json.dumps(parsed, indent=2)
        except json.JSONDecodeError:
            pretty = raw

        msg = MonitorMessage(
            channel=channel,
            topic=topic,
            payload=pretty,
        )
        if self._on_message:
            self._on_message(msg)

    # ── Logging helper ─────────────────────────────────────────────────────

    def _log(self, text: str) -> None:
        logger.info(text)
        if self._on_log:
            self._on_log(text)
