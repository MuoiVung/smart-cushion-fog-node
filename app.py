"""
Smart Cushion Fog Node – Main Application Entry Point.

This module wires all components together and runs the main asyncio event loop.

Data pipeline (per sensor reading):
  ESP32 (MQTT cushion/raw)
    └─► MQTTClient._on_message()   [paho thread]
          └─► asyncio.Queue        [thread-safe bridge]
                └─► _message_processor()    [asyncio task]
                      ├─► Preprocessor.is_person_present()
                      ├─► Preprocessor.extract_features()
                      ├─► InferenceEngine.predict()
                      ├─► SessionManager.add_reading()  → alert decision
                      ├─► MQTTClient.publish_control()  [if alert]
                      └─► WebSocketServer.broadcast()

Periodic cloud sync task (every CLOUD_SYNC_INTERVAL seconds):
  SessionManager.get_sync_payload() → CloudSync.publish()
"""

import asyncio
import json
import logging
import signal
import sys

from ai.inference_engine import InferenceEngine
from ai.preprocessor import Preprocessor
from config.settings import settings
from core.cloud_sync import CloudSync
from core.mqtt_client import MQTTClient
from core.session_manager import SessionManager
from core.websocket_server import WebSocketServer
from data.schema import ControlCommand, PostureLabel, RawMessage, WebSocketBroadcast, AggregatedSensorReading
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


class FogApplication:
    """
    Top-level orchestrator of the Smart Cushion Fog Node.

    Responsibilities:
    - Initialise all components with configuration from settings.
    - Run the MQTT, WebSocket, and cloud sync concurrently.
    - Handle graceful shutdown on SIGTERM / SIGINT (Ctrl+C).
    """

    def __init__(self) -> None:
        # asyncio queue bridges paho's callback thread to our async pipeline
        self._message_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._current_sensors = AggregatedSensorReading()

        # ── Component initialisation ────────────────────────────────────────
        # (MQTT client needs the loop reference; we pass it in run())
        self._ws_server      = WebSocketServer(settings)
        self._cloud_sync     = CloudSync(settings)
        self._session_manager = SessionManager(
            window_seconds=settings.cloud_sync_interval,
            incorrect_alert_threshold=settings.incorrect_posture_alert_threshold,
        )
        self._preprocessor = Preprocessor(
            temperature_threshold=settings.temperature_threshold,
        )
        self._inference = InferenceEngine(model_path=settings.model_path)

    # ── Application lifecycle ──────────────────────────────────────────────

    async def run(self) -> None:
        """
        Start all subsystems and run until a shutdown signal is received.
        """
        setup_logging()
        self._print_banner()
        self._running = True

        # MQTTClient needs the running event loop to forward messages safely
        self._loop = asyncio.get_running_loop()
        mqtt_client = MQTTClient(settings, self._loop, self._message_queue)

        # ── Graceful shutdown hooks ─────────────────────────────────────────
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._loop.add_signal_handler(sig, self._request_shutdown)
            except NotImplementedError:
                pass

        # ── Start components ────────────────────────────────────────────────
        mqtt_client.start()

        if settings.cloud_enabled:
            await self._cloud_sync.connect()

        # Run all async tasks concurrently inside a TaskGroup
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._ws_server.start(),       name="websocket-server")
                tg.create_task(self._message_processor(),     name="message-processor")
                tg.create_task(self._cloud_sync_loop(),       name="cloud-sync")
                tg.create_task(self._shutdown_watcher(),      name="shutdown-watcher")
        except* asyncio.CancelledError:
            pass
        finally:
            logger.info("Stopping MQTT client...")
            mqtt_client.stop()
            if settings.cloud_enabled:
                await self._cloud_sync.disconnect()
            logger.info("Fog Node stopped cleanly. Goodbye!")

    # ── Async tasks ────────────────────────────────────────────────────────

    async def _message_processor(self) -> None:
        """
        Consume raw MQTT payloads from the queue and run the full AI pipeline.
        Runs continuously until self._running is set to False.
        """
        logger.info("Message processor started")

        while self._running:
            try:
                # Drain the queue with a timeout so we can check _running regularly
                payload: bytes = await asyncio.wait_for(
                    self._message_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            try:
                await self._process_sensor_data(payload)
            except Exception:
                logger.exception("Unhandled error in message processor")
            finally:
                self._message_queue.task_done()

    async def _cloud_sync_loop(self) -> None:
        """Periodically publish session summary to AWS IoT Core."""
        if not settings.cloud_enabled:
            logger.info("Cloud sync disabled (CLOUD_ENABLED=false). Task is idle.")
            # Block until cancelled so TaskGroup doesn't error on a quick return
            await asyncio.Event().wait()
            return

        logger.info(f"Cloud sync loop started (interval={settings.cloud_sync_interval}s)")
        while self._running:
            await asyncio.sleep(settings.cloud_sync_interval)
            if not self._running:
                break
            sync_payload = self._session_manager.get_sync_payload(settings.device_id)
            await self._cloud_sync.publish(sync_payload)

    async def _shutdown_watcher(self) -> None:
        """Wait for _running to become False, then cancel all peer tasks."""
        while self._running:
            await asyncio.sleep(0.5)
        # Cancel the remaining tasks in the TaskGroup
        for task in asyncio.all_tasks():
            if task.get_name() in {"websocket-server", "message-processor", "cloud-sync"}:
                task.cancel()

    # ── Core pipeline ──────────────────────────────────────────────────────

    async def _process_sensor_data(self, raw_bytes: bytes) -> None:
        """
        Full AI pipeline for a single sensor reading.

        Steps:
          1. Parse and validate JSON.
          2. Detect human presence from temperature.
          3. Normalise FSR features.
          4. Run AI inference.
          5. Evaluate alert threshold.
          6. Send vibration command if threshold exceeded.
          7. Broadcast result to WebSocket clients.
        """
        # Step 1 – Parse + validate partial payload
        try:
            raw_dict = json.loads(raw_bytes)
            raw_msg  = RawMessage.model_validate(raw_dict)
        except Exception as exc:
            logger.warning(f"Invalid sensor message, skipping: {exc}")
            return

        # Update aggregated state with new values (ignore None)
        updated_data = {k: v for k, v in raw_msg.sensors.model_dump().items() if v is not None}
        for k, v in updated_data.items():
            setattr(self._current_sensors, k, v)

        sensors = self._current_sensors

        # Step 2 – Human presence detection
        person_detected = self._preprocessor.is_person_present(sensors)

        if not person_detected:
            posture    = PostureLabel.UNKNOWN
            confidence = 1.0
            processed_features = None
        else:
            # Step 3 – Feature extraction
            features = self._preprocessor.extract_features(sensors)

            # Step 4 – AI inference
            posture, confidence = self._inference.predict(features)
            processed_features = features.tolist()

        # Step 5 – Session tracking + alert evaluation
        should_alert = self._session_manager.add_reading(
            posture=posture,
            person_detected=person_detected,
            timestamp=raw_msg.timestamp,
        )

        # Step 6 – Send vibration command if needed
        alert_sent = False
        if should_alert:
            cmd = ControlCommand(
                command="vibrate",
                duration_ms=settings.vibration_duration_ms,
                reason=posture.value,
            )
            # Find the mqtt client – it's captured in the closure via the local var
            # We re-use the module-level reference instead
            _get_mqtt_client().publish_control(cmd)
            alert_sent = True
            logger.info(f"[ALERT] Vibration sent – bad posture: {posture.value}")

        # Step 7 – Broadcast to WebSocket clients
        broadcast = WebSocketBroadcast(
            timestamp=raw_msg.timestamp,
            posture=posture,
            confidence=round(confidence, 4),
            person_detected=person_detected,
            sensors=sensors,
            features=processed_features,
            alert_sent=alert_sent,
        )
        await self._ws_server.broadcast(broadcast.model_dump())

        logger.debug(
            f"Pipeline done – posture={posture.value}, "
            f"conf={confidence:.2f}, person={person_detected}, alert={alert_sent}, "
            f"ws_clients={self._ws_server.connected_count}"
        )

    # ── Shutdown ───────────────────────────────────────────────────────────

    def _request_shutdown(self) -> None:
        logger.info("Shutdown signal received – stopping Fog Node...")
        self._running = False

    # ── Banner ─────────────────────────────────────────────────────────────

    @staticmethod
    def _print_banner() -> None:
        border = "=" * 56
        logger.info(border)
        logger.info("  Smart Cushion Fog Node")
        logger.info(f"  MQTT Broker  : {settings.mqtt_host}:{settings.mqtt_port}")
        logger.info(f"  WebSocket    : ws://{settings.ws_host}:{settings.ws_port}")
        logger.info(f"  AI Model     : {settings.model_path}")
        logger.info(f"  Cloud Sync   : {'ENABLED' if settings.cloud_enabled else 'disabled'}")
        logger.info(border)


# ---------------------------------------------------------------------------
# Module-level MQTT client reference (set once in run(), used in pipeline)
# ---------------------------------------------------------------------------
_mqtt_ref: MQTTClient | None = None


def _get_mqtt_client() -> MQTTClient:
    if _mqtt_ref is None:
        raise RuntimeError("MQTT client not initialised")
    return _mqtt_ref


# Override run() to capture the MQTT client reference
_orig_run = FogApplication.run


async def _patched_run(self: FogApplication) -> None:
    global _mqtt_ref
    setup_logging()
    self._print_banner()
    self._running = True
    self._loop = asyncio.get_running_loop()

    mqtt_client = MQTTClient(settings, self._loop, self._message_queue)
    _mqtt_ref = mqtt_client

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            self._loop.add_signal_handler(sig, self._request_shutdown)
        except NotImplementedError:
            pass

    mqtt_client.start()

    if settings.cloud_enabled:
        await self._cloud_sync.connect()

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._ws_server.start(),   name="websocket-server")
            tg.create_task(self._message_processor(), name="message-processor")
            tg.create_task(self._cloud_sync_loop(),   name="cloud-sync")
            tg.create_task(self._shutdown_watcher(),  name="shutdown-watcher")
    except* asyncio.CancelledError:
        pass
    finally:
        logger.info("Stopping MQTT client...")
        mqtt_client.stop()
        if settings.cloud_enabled:
            await self._cloud_sync.disconnect()
        logger.info("Fog Node stopped cleanly. Goodbye!")


FogApplication.run = _patched_run  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if sys.version_info < (3, 11):
        print("Python 3.11+ is required (uses asyncio.TaskGroup).")
        sys.exit(1)

    app = FogApplication()
    asyncio.run(app.run())
