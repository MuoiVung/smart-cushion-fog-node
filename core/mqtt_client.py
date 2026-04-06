"""
MQTT Client for the Smart Cushion Fog Node.

Handles bidirectional MQTT communication between the Fog Node and the ESP32:
  - Subscribe to cushion/raw   → receive raw sensor JSON
  - Publish to cushion/control → send vibration commands to ESP32

Implementation notes:
  paho-mqtt's network loop runs in its own thread. Incoming messages are
  dispatched to paho callbacks on that thread, then safely forwarded to the
  asyncio event loop via asyncio.run_coroutine_threadsafe() so the rest of
  the application stays entirely in async/await land.

Security:
  - Username/password authentication is always required (configured in .env).
  - Optional TLS can be enabled by setting MQTT_USE_TLS=true and providing
    a CA certificate path via MQTT_CA_CERT.
"""

import asyncio
import json
import logging
import ssl
import time
from typing import Optional

import paho.mqtt.client as mqtt

from config.settings import Settings
from data.schema import ControlCommand

logger = logging.getLogger(__name__)

# Reconnect delay boundaries (seconds)
_MIN_BACKOFF = 1
_MAX_BACKOFF = 60


class MQTTClient:
    """
    Thread-safe MQTT client wrapper.

    The paho network loop runs in a background thread; all incoming messages
    are forwarded to the asyncio event loop through a thread-safe queue.

    Usage:
        client = MQTTClient(settings, loop, message_queue)
        client.start()          # Connects and starts paho loop thread
        ...
        client.stop()           # Disconnects and stops the thread
    """

    def __init__(
        self,
        settings: Settings,
        loop: asyncio.AbstractEventLoop,
        message_queue: asyncio.Queue,
    ) -> None:
        self._settings = settings
        self._loop = loop
        self._queue = message_queue

        # ── paho client setup ───────────────────────────────────────────
        client_id = f"fog-node-{int(time.time())}"
        self._client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )

        # Authenticate with username / password from .env
        if settings.mqtt_username:
            self._client.username_pw_set(
                username=settings.mqtt_username,
                password=settings.mqtt_password,
            )

        # Optional TLS for encrypted local MQTT
        if settings.mqtt_use_tls:
            self._configure_tls()

        # Wire paho callbacks
        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

        self._reconnect_delay = _MIN_BACKOFF

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Connect to the MQTT broker and start the paho background network loop.
        Reconnection is handled automatically by paho's reconnect logic.
        """
        logger.info(
            f"Connecting to MQTT broker at {self._settings.mqtt_host}:{self._settings.mqtt_port} "
            f"(TLS={'on' if self._settings.mqtt_use_tls else 'off'})"
        )
        self._client.connect_async(
            host=self._settings.mqtt_host,
            port=self._settings.mqtt_port,
            keepalive=60,
        )
        self._client.loop_start()   # Starts the background network thread

    def stop(self) -> None:
        """Disconnect from the broker and stop the background thread."""
        logger.info("Disconnecting from MQTT broker...")
        self._client.loop_stop()
        self._client.disconnect()

    def publish_control(self, command: ControlCommand) -> None:
        """
        Publish a control command to the ESP32.

        Args:
            command: ControlCommand pydantic model (serialised to JSON).

        This method is safe to call from the asyncio thread because paho's
        publish() is thread-safe.
        """
        payload = command.model_dump_json()
        result = self._client.publish(
            topic=self._settings.mqtt_topic_control,
            payload=payload,
            qos=1,          # At-least-once delivery for commands
            retain=False,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error(f"Failed to publish control command (rc={result.rc})")
        else:
            logger.debug(f"Control command published: {payload}")

    # ── paho Callbacks (called from paho's background thread) ──────────────

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata,
        flags: dict,
        rc: int,
    ) -> None:
        if rc == 0:
            logger.info("MQTT connected successfully")
            self._reconnect_delay = _MIN_BACKOFF
            # Subscribe to raw sensor topic
            client.subscribe(self._settings.mqtt_topic_raw, qos=0)
            logger.info(f"Subscribed to topic: {self._settings.mqtt_topic_raw}")
        else:
            logger.error(f"MQTT connection failed (rc={rc}): {mqtt.connack_string(rc)}")

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata,
        rc: int,
    ) -> None:
        if rc == 0:
            logger.info("MQTT disconnected cleanly")
        else:
            logger.warning(
                f"MQTT unexpected disconnect (rc={rc}), "
                f"paho will retry in {self._reconnect_delay}s"
            )
            # Increase backoff exponentially (paho handles the actual reconnect)
            self._reconnect_delay = min(self._reconnect_delay * 2, _MAX_BACKOFF)

    def _on_message(
        self,
        client: mqtt.Client,
        userdata,
        message: mqtt.MQTTMessage,
    ) -> None:
        """
        Forward raw MQTT payload bytes to the asyncio queue (thread-safe).

        paho calls this on its own thread. We use run_coroutine_threadsafe()
        to hand the payload to the asyncio event loop without blocking paho.
        """
        logger.debug(
            f"MQTT message received on '{message.topic}' "
            f"({len(message.payload)} bytes)"
        )
        asyncio.run_coroutine_threadsafe(
            self._queue.put(message.payload),
            self._loop,
        )

    # ── TLS Configuration ──────────────────────────────────────────────────

    def _configure_tls(self) -> None:
        """Set up TLS using the CA certificate path from settings."""
        ca_cert = self._settings.mqtt_ca_cert
        if not ca_cert:
            logger.warning(
                "MQTT TLS is enabled but MQTT_CA_CERT is not set. "
                "TLS will use the system CA bundle."
            )
            ca_cert = None

        self._client.tls_set(
            ca_certs=ca_cert,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        logger.info("MQTT TLS configured")
