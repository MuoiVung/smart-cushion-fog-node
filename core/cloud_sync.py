"""
Cloud Sync Module for the Smart Cushion Fog Node.

Publishes periodic session summary payloads to AWS IoT Core via MQTT over TLS.
This is the ONLY data that leaves the local network, ensuring that
raw sensor readings and personal biometric data stay on-premise.

Security:
  - Mutual TLS authentication using X.509 certificates issued by AWS IoT.
  - Certificate files are stored in the gitignored certs/ directory.
  - Enable this module by setting CLOUD_ENABLED=true and filling in
    all AWS_* variables in your .env file.

Disabling:
  Set CLOUD_ENABLED=false (the default) to completely skip cloud sync.
  The rest of the system operates normally without it.
"""

import asyncio
import json
import logging
import ssl
from typing import Optional

import paho.mqtt.client as mqtt

from config.settings import Settings
from data.schema import CloudSyncPayload

logger = logging.getLogger(__name__)

# AWS IoT Core MQTT port (TLS)
_AWS_MQTT_PORT = 8883


class CloudSync:
    """
    Publishes CloudSyncPayload messages to AWS IoT Core using MQTT over TLS.

    Internally uses paho-mqtt with a certificate-based TLS configuration.
    publish() is an async wrapper that runs the blocking paho call in the
    default executor to avoid blocking the event loop.

    Usage:
        sync = CloudSync(settings)
        await sync.connect()
        await sync.publish(payload)
        await sync.disconnect()
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Optional[mqtt.Client] = None
        self._connected = False

    # ── Public API ─────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Connect to AWS IoT Core.
        Must be called before publish() if CLOUD_ENABLED is true.
        """
        if not self._settings.cloud_enabled:
            return

        logger.info(f"Connecting to AWS IoT Core at {self._settings.aws_endpoint}:{_AWS_MQTT_PORT}")

        # Set up TLS context with AWS IoT certificates
        ssl_context = self._build_ssl_context()

        self._client = mqtt.Client(
            client_id=self._settings.aws_client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        self._client.tls_set_context(ssl_context)
        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        # Connect in a background thread (blocking call)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.connect(
                host=self._settings.aws_endpoint,
                port=_AWS_MQTT_PORT,
                keepalive=60,
            ),
        )
        self._client.loop_start()

        # Wait a brief moment for the connection to establish
        await asyncio.sleep(1.5)

    async def disconnect(self) -> None:
        """Disconnect from AWS IoT Core."""
        if self._client and self._connected:
            self._client.loop_stop()
            self._client.disconnect()
            logger.info("Disconnected from AWS IoT Core")

    async def publish(self, payload: CloudSyncPayload) -> None:
        """
        Publish a session summary to AWS IoT Core.

        Args:
            payload: CloudSyncPayload to serialise and send.
        """
        if not self._settings.cloud_enabled or not self._client or not self._connected:
            logger.debug("Cloud sync skipped (disabled or not connected)")
            return

        message = payload.model_dump_json()
        loop = asyncio.get_event_loop()

        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._client.publish(
                    topic=self._settings.aws_topic_sync,
                    payload=message,
                    qos=1,
                ),
            )
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info(
                    f"Cloud sync published to '{self._settings.aws_topic_sync}': "
                    f"correct={payload.correct_seconds}s, "
                    f"incorrect={payload.incorrect_seconds}s"
                )
            else:
                logger.error(f"Cloud publish failed (rc={result.rc})")
        except Exception as exc:
            logger.error(f"Cloud sync error: {exc}", exc_info=True)

    # ── paho Callbacks ─────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc: int) -> None:
        if rc == 0:
            self._connected = True
            logger.info("AWS IoT Core connected successfully")
        else:
            logger.error(f"AWS IoT Core connection failed (rc={rc})")

    def _on_disconnect(self, client, userdata, rc: int) -> None:
        self._connected = False
        if rc != 0:
            logger.warning(f"AWS IoT Core disconnected unexpectedly (rc={rc})")
        else:
            logger.info("AWS IoT Core disconnected cleanly")

    # ── TLS Setup ─────────────────────────────────────────────────────────

    def _build_ssl_context(self) -> ssl.SSLContext:
        """
        Build an SSL context for AWS IoT Core mutual TLS authentication.

        Requires three files (all gitignored, downloaded from AWS Console):
          AWS_CERT_PATH  – Device certificate
          AWS_KEY_PATH   – Private key
          AWS_CA_PATH    – Amazon Root CA
        """
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        context.load_verify_locations(cafile=self._settings.aws_ca_path)
        context.load_cert_chain(
            certfile=self._settings.aws_cert_path,
            keyfile=self._settings.aws_key_path,
        )
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return context
