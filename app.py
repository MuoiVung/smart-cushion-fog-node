"""
Smart Cushion Fog Node – Main Application Entry Point.

This module wires all components together and runs the main asyncio event loop.

Data pipeline (per sensor reading):
  ESP32 (MQTT cushion/raw)
    └─► MQTTClient._on_message()      [paho thread]
          └─► asyncio.Queue           [thread-safe bridge]
                └─► _message_processor()    [asyncio task]
                      ├─► Preprocessor.is_person_present()
                      ├─► Preprocessor.extract_features()
                      ├─► InferenceEngine.predict()
                      ├─► SessionManager.add_reading()   → alert decision
                      ├─► MQTTClient.publish_control()   [if alert – Interface 05]
                      └─► WebSocketServer.broadcast()    [Interface 02]

Periodic cloud sync task (every CLOUD_SYNC_INTERVAL seconds):
  SessionManager.get_sync_payload() → CloudSync.publish()  [Interface 03]
"""

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone

from ai.inference_engine import InferenceEngine
from ai.preprocessor import Preprocessor
from config.settings import settings
from core.cloud_sync import CloudSync
from core.mqtt_client import MQTTClient
from core.session_manager import SessionManager
from core.websocket_server import WebSocketServer
from data.schema import (
    AggregatedSensorReading,
    AlertStatus,
    ControlCommand,
    FogRealtimeUpdate,
    OccupancyState,
    PostureLabel,
    RawMessage,
    GOOD_POSTURES,
)
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

        # Aggregated sensor state (merged from both ESP32 devices)
        self._current_sensors = AggregatedSensorReading()

        # Session-level counters
        self._session_id: str = ""
        self._session_start: datetime | None = None
        self._alert_count: int = 0
        self._alert_status: AlertStatus = AlertStatus.IDLE
        self._alert_active: bool = False   # physical motor state

        # ── Component initialisation ────────────────────────────────────────
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
        """Start all subsystems and run until a shutdown signal is received."""
        setup_logging()
        self._print_banner()
        self._running = True

        self._loop = asyncio.get_running_loop()
        mqtt_client = MQTTClient(settings, self._loop, self._message_queue)

        global _mqtt_ref
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
        """Consume raw MQTT payloads from the queue and run the full AI pipeline."""
        logger.info("Message processor started")
        while self._running:
            try:
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
        for task in asyncio.all_tasks():
            if task.get_name() in {"websocket-server", "message-processor", "cloud-sync"}:
                task.cancel()

    # ── Core pipeline ──────────────────────────────────────────────────────

    async def _process_sensor_data(self, raw_bytes: bytes) -> None:
        """
        Full AI pipeline for a single sensor reading.

        Steps:
          1. Parse and validate JSON from ESP32.
          2. Merge partial readings into aggregated sensor state.
          3. Detect human presence from temperature threshold.
          4. Extract features + run AI inference (if person present).
          5. Evaluate alert threshold via SessionManager.
          6. Send vibration command to ESP32-1 if threshold exceeded (Interface 05).
          7. Build Interface 02 payload and broadcast via WebSocket.
        """
        # Step 1 – Parse
        try:
            raw_dict = json.loads(raw_bytes)
            raw_msg  = RawMessage.model_validate(raw_dict)
        except Exception as exc:
            logger.warning(f"Invalid sensor message, skipping: {exc}")
            return

        # Step 2 – Merge partial readings (each ESP32 only sends its own sensors)
        updated = {k: v for k, v in raw_msg.sensors.model_dump().items() if v is not None}
        for k, v in updated.items():
            setattr(self._current_sensors, k, v)

        sensors = self._current_sensors

        # Step 3 – Occupancy / presence detection
        person_detected = self._preprocessor.is_person_present(sensors)

        if not person_detected:
            posture    = PostureLabel.UNKNOWN
            occupancy  = OccupancyState.EMPTY
            confidence = 1.0
        else:
            occupancy = OccupancyState.OCCUPIED

            # Step 4 – Feature extraction + AI inference
            features           = self._preprocessor.extract_features(sensors)
            posture, confidence = self._inference.predict(features)

        # Step 5 – Session tracking + alert evaluation
        should_alert = self._session_manager.add_reading(
            posture=posture,
            person_detected=person_detected,
            timestamp=raw_msg.timestamp,
        )

        # Step 6 – Vibration command (Interface 05)
        if should_alert:
            cmd = ControlCommand(
                device_id="esp32-1",
                command="vibrate",
                active=True,
                pattern="short_triple",
                intensity=settings.vibration_intensity if hasattr(settings, "vibration_intensity") else 200,
            )
            _get_mqtt_client().publish_control(cmd)
            self._alert_active = True
            self._alert_count += 1
            self._alert_status = AlertStatus.WARNING
            logger.info(f"[ALERT] Vibration sent – posture: {posture.value}")
        else:
            # Motor is physically off between alerts
            if self._alert_active and posture in GOOD_POSTURES:
                self._alert_active = False
                self._alert_status = AlertStatus.COOLDOWN
            elif posture in GOOD_POSTURES:
                self._alert_status = AlertStatus.IDLE

        # Manage session start
        if person_detected and self._session_start is None:
            self._session_start = datetime.now(timezone.utc)
            self._session_id    = FogRealtimeUpdate().session_id   # auto-generate

        if not person_detected and self._session_start is not None:
            self._session_start = None
            self._session_id    = ""
            self._alert_count   = 0
            self._alert_status  = AlertStatus.IDLE
            self._alert_active  = False

        # Compute session duration
        session_duration_sec = 0
        session_start_iso    = ""
        if self._session_start:
            session_duration_sec = int(
                (datetime.now(timezone.utc) - self._session_start).total_seconds()
            )
            session_start_iso = self._session_start.isoformat()

        # Step 7 – Broadcast Interface 02 payload
        broadcast = FogRealtimeUpdate(
            device_id=settings.device_id,
            session_id=self._session_id,
            session_start_time_iso=session_start_iso,
            occupancy_state=occupancy,
            posture=posture,
            temperature=round(sensors.temperature, 1),
            alert_active=self._alert_active,
            alert_status=self._alert_status,
            alert_count=self._alert_count,
            session_duration_sec=session_duration_sec,
            sensors_heatmap_pct=sensors.as_heatmap_pct(),
        )
        await self._ws_server.broadcast(broadcast.model_dump())

        logger.debug(
            f"Pipeline done – posture={posture.value}, "
            f"occupancy={occupancy.value}, alert={self._alert_active}, "
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
# Module-level MQTT client reference
# ---------------------------------------------------------------------------
_mqtt_ref: MQTTClient | None = None


def _get_mqtt_client() -> MQTTClient:
    if _mqtt_ref is None:
        raise RuntimeError("MQTT client not initialised")
    return _mqtt_ref


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if sys.version_info < (3, 11):
        print("Python 3.11+ is required (uses asyncio.TaskGroup).")
        sys.exit(1)

    app = FogApplication()
    asyncio.run(app.run())
