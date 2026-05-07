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
from typing import Optional, TYPE_CHECKING

import paho.mqtt.client as mqtt

from config.settings import Settings
from pydantic import BaseModel
from data.schema import (
    CloudEventRecord,
    CloudTelemetryRecord,
    CloudSummaryRecord,
)

if TYPE_CHECKING:
    from core.local_db import LocalDB

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

    def __init__(self, settings: Settings, local_db: Optional["LocalDB"] = None) -> None:
        self._settings  = settings
        self._local_db  = local_db
        self._client: Optional[mqtt.Client] = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """True when the AWS IoT Core MQTT connection is live."""
        return self._connected

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

    async def _publish_generic(self, payload: BaseModel, topic_template: str) -> None:
        """Publish a Pydantic payload; queue locally if cloud is unavailable."""
        topic       = topic_template.format(device_id=self._settings.device_id)
        message     = payload.model_dump_json()
        record_type = getattr(payload, "record_type", "unknown")

        logger.debug(f"[CloudSync] Attempting to publish {record_type} to {topic}")

        # ── Not connected or Disabled: queue for later ────────────────────────
        is_cloud_ready = self._settings.cloud_enabled and self._client and self._connected
        
        if not is_cloud_ready:
            if self._settings.cloud_enabled and self._local_db:
                self._local_db.enqueue(record_type, topic, message)
                count = self._local_db.get_pending_count()
                logger.warning(
                    f"[CloudQueue] Cloud OFFLINE – {record_type} enqueued to LocalDB "
                    f"(Total pending: {count})"
                )
            else:
                logger.debug(f"[CloudSync] {record_type} skipped (Cloud enabled={self._settings.cloud_enabled}, Connected={self._connected})")
            return

        # ── Try to publish ────────────────────────────────────────────────
        loop = asyncio.get_event_loop()
        try:
            logger.debug(f"[CloudSync] Sending {record_type} to AWS IoT Core...")
            result = await loop.run_in_executor(
                None,
                lambda: self._client.publish(topic=topic, payload=message, qos=1),
            )
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info(f"✅ [CloudSync] Successfully published {record_type} to '{topic}'")
            else:
                logger.error(f"❌ [CloudSync] Publish failed (rc={result.rc}) for {record_type}. Enqueueing to LocalDB.")
                if self._local_db:
                    self._local_db.enqueue(record_type, topic, message)
        except Exception as exc:
            logger.error(f"🔥 [CloudSync] Critical error during publish: {exc}. Enqueueing to LocalDB.")
            if self._local_db:
                self._local_db.enqueue(record_type, topic, message)

    async def publish_event(self, payload: CloudEventRecord) -> None:
        await self._publish_generic(payload, self._settings.aws_topic_event)

    async def publish_telemetry(self, payload: CloudTelemetryRecord) -> None:
        await self._publish_generic(payload, self._settings.aws_topic_telemetry)

    async def publish_summary(self, payload: CloudSummaryRecord) -> None:
        await self._publish_generic(payload, self._settings.aws_topic_summary)

    async def drain_queue(self) -> tuple[int, int]:
        """
        Retry all pending events stored in the local DB queue.

        Called automatically by the retry loop in app.py when the cloud
        connection is re-established.

        Returns:
            Tuple of (sent_count, failed_count).
        """
        if not self._local_db or not self._connected:
            return 0, 0

        pending = self._local_db.get_pending(limit=100)
        if not pending:
            return 0, 0

        sent, failed = 0, 0
        loop = asyncio.get_event_loop()
        logger.info(f"[CloudQueue] Draining {len(pending)} pending events…")

        for row in pending:
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda r=row: self._client.publish(
                        topic=r["topic"], payload=r["payload"], qos=1
                    ),
                )
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    self._local_db.mark_sent(row["id"])
                    sent += 1
                else:
                    self._local_db.increment_retry(row["id"])
                    failed += 1
            except Exception as exc:
                logger.error(f"[CloudQueue] Retry failed id={row['id']}: {exc}")
                self._local_db.increment_retry(row["id"])
                failed += 1

        logger.info(f"[CloudQueue] Drain complete: {sent} sent, {failed} failed")
        return sent, failed

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
