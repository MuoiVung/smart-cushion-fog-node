"""
Smart Cushion Fog Node – Main Application Entry Point.

Data pipeline (per sensor reading from ESP32 via MQTT cushion/raw):
  RawMessage (JSON)
    └─► Merge into AggregatedSensorReading
          └─► InferenceEngine.predict(raw_9_values)   [11-label Keras CNN]
                └─► PostureLabel (empty / object / NUP / LF / LB / ...)
                      ├─► OccupancyState derived from label
                      ├─► SessionManager.add_reading()  → alert decision
                      ├─► MQTTClient.publish_control()   [Interface 05, if alert]
                      └─► WebSocketServer.broadcast()    [Interface 02]

Periodic cloud sync:
  Every CLOUD_SYNC_INTERVAL seconds:
    SessionManager.get_sync_payload() → CloudSync.publish() [Interface 03]
"""

import asyncio
from collections import Counter, deque
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ai.inference_engine import InferenceEngine
from ai.preprocessor import Preprocessor
from config.settings import settings
from core.cloud_sync import CloudSync
from core.cloud_ws_relay import CloudWsRelay
from core.discovery_service import DiscoveryService
from core.local_db import LocalDB
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
    CloudEventRecord,
    CloudTelemetryRecord,
    EventType,
    GOOD_POSTURES,
    SITTING_POSTURES,
    occupancy_from_label,
    generate_session_id,
)
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


class FogApplication:
    """Top-level orchestrator of the Smart Cushion Fog Node."""

    def __init__(self) -> None:
        self._message_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

        # Aggregated sensor state (merged from both ESP32 devices)
        self._current_sensors = AggregatedSensorReading()

        # Session-level counters
        self._session_id:       str              = ""
        self._session_start:    datetime | None  = None
        self._alert_count:      int              = 0
        self._alert_status:     AlertStatus      = AlertStatus.IDLE
        self._alert_active:     bool             = False
        self._consecutive_bad:  int              = 0   # consecutive bad posture readings
        
        # Runtime Config (Always starts as True by default)
        self._vibration_enabled: bool = True

        # ── Components ──────────────────────────────────────────────────────
        self._ws_server       = WebSocketServer(settings)
        self._local_db        = LocalDB()
        self._cloud_sync      = CloudSync(settings, local_db=self._local_db)
        self._cloud_ws_relay  = CloudWsRelay(settings.cloud_ws_url, settings.device_id)
        self._session_manager = SessionManager()
        self._preprocessor = Preprocessor()   # no parameters needed anymore

        # Read model paths from DB (source of truth); fall back to .env on first run
        _model_type  = self._local_db.get_config("model_type",  "keras") # We don't have settings.model_type yet, default to keras
        _model_path  = self._local_db.get_config("model_path",  settings.model_path)
        _scaler_path = self._local_db.get_config("scaler_path", settings.scaler_path)
        self._inference = InferenceEngine(
            model_type  = _model_type,
            model_path  = _model_path,
            scaler_path = _scaler_path,
        )

        # ── Prediction smoothing & confidence filter ─────────────────────────
        # Values are persisted in LocalDB; update via cushion/fog/config MQTT.
        self._min_confidence        = float(self._local_db.get_config("min_confidence",       "0.70"))
        self._smoothing_window_size = int(self._local_db.get_config("smoothing_window_size",  "10"))
        self._smoothing_min_votes   = int(self._local_db.get_config("smoothing_min_votes",    "7"))
        self._prediction_window: deque[str] = deque(maxlen=self._smoothing_window_size)
        self._last_stable_posture: PostureLabel  = PostureLabel.EMPTY
        self._last_stable_confidence: float      = 1.0

    # ── Application lifecycle ──────────────────────────────────────────────

    async def run(self) -> None:
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
            try:
                await self._cloud_sync.connect()
                # Immediately drain any events queued while we were offline
                if self._local_db.get_pending_count() > 0:
                    logger.info("[CloudSync] Connected – draining offline queue immediately")
                    await self._cloud_sync.drain_queue()
            except Exception as exc:
                logger.error(
                    f"[CloudSync] Initial connection failed: {exc}. "
                    "Running in offline mode – will retry every 60s."
                )
        await self._cloud_ws_relay.start()

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._ws_server.start(),       name="websocket-server")
                tg.create_task(self._message_processor(),     name="message-processor")
                tg.create_task(self._cloud_sync_loop(),       name="cloud-sync")
                tg.create_task(self._cloud_retry_loop(),      name="cloud-retry")
                tg.create_task(self._shutdown_watcher(),      name="shutdown-watcher")
        except* asyncio.CancelledError:
            pass
        finally:
            logger.info("Stopping MQTT client...")
            mqtt_client.stop()
            await self._cloud_ws_relay.stop()
            if settings.cloud_enabled:
                await self._cloud_sync.disconnect()
            logger.info("Fog Node stopped cleanly. Goodbye!")

    # ── Async tasks ────────────────────────────────────────────────────────

    async def _message_processor(self) -> None:
        logger.info("Message processor started")
        while self._running:
            try:
                # MQTTClient now puts (topic, payload) into the queue
                msg_tuple = await asyncio.wait_for(
                    self._message_queue.get(), timeout=1.0
                )
                topic, payload = msg_tuple
            except asyncio.TimeoutError:
                continue
            
            try:
                if topic == settings.mqtt_topic_raw:
                    await self._process_sensor_data(payload)
                elif topic == "cushion/fog/config":
                    await self._process_config_data(payload)
                elif topic == "cushion/fog/model_reload":
                    await self._process_model_reload(payload)
            except Exception:
                logger.exception(f"Error processing message on topic {topic}")
            finally:
                self._message_queue.task_done()

    async def _cloud_sync_loop(self) -> None:
        """Periodically publish telemetry to AWS IoT Core while a session is active."""
        if not settings.cloud_enabled:
            logger.info("Cloud sync is disabled")
            await asyncio.Event().wait()
            return

        # NOTE: initial connection is handled in run(); reconnection in _cloud_retry_loop
        logger.info(f"Cloud telemetry loop started (interval={settings.cloud_sync_interval}s)")
        while self._running:
            await asyncio.sleep(settings.cloud_sync_interval)
            if not self._running:
                break

            if self._session_start is not None and self._session_id:
                telemetry = CloudTelemetryRecord(
                    device_id=settings.device_id,
                    session_id=self._session_id,
                    fog_timestamp_iso=datetime.now(timezone.utc).isoformat(),
                    occupancy_state=OccupancyState.OCCUPIED,
                    posture=PostureLabel.NUP,
                    alert_active=self._alert_active,
                )
                await self._cloud_sync.publish_telemetry(telemetry)

    async def _shutdown_watcher(self) -> None:
        while self._running:
            await asyncio.sleep(0.5)
        for task in asyncio.all_tasks():
            if task.get_name() in {"websocket-server", "message-processor", "cloud-sync", "cloud-retry"}:
                task.cancel()

    async def _cloud_retry_loop(self) -> None:
        """
        Every 60s: reconnect if offline, purge old events, drain pending queue.
        """
        logger.info("Cloud retry loop started")
        while self._running:
            await asyncio.sleep(60)
            if not self._running:
                break

            # 1. Reconnect if enabled but offline
            if settings.cloud_enabled and not self._cloud_sync.is_connected:
                try:
                    logger.info("[CloudRetry] Attempting to reconnect to AWS IoT Core…")
                    await self._cloud_sync.connect()
                    if self._cloud_sync.is_connected:
                        logger.info("[CloudRetry] ✅ Reconnected – draining offline queue")
                        await self._cloud_sync.drain_queue()
                        continue   # skip purge this cycle, next cycle will handle it
                except Exception as exc:
                    logger.debug(f"[CloudRetry] Reconnection failed: {exc}")
                    continue

            # 2. Auto-purge events older than configured retention period
            retention_days = int(self._local_db.get_config("cloud_queue_retention_days", "7"))
            self._local_db.purge_old(retention_days)

            # 3. Drain queue if cloud is connected and there are pending items
            pending = self._local_db.get_pending_count()
            if pending > 0 and self._cloud_sync.is_connected:
                logger.info(f"[CloudRetry] {pending} pending events, draining…")
                sent, failed = await self._cloud_sync.drain_queue()
                if sent:
                    logger.info(f"[CloudRetry] ✅ Sent {sent} queued events to cloud")
                if failed:
                    logger.warning(f"[CloudRetry] ⚠️ {failed} events still failed, will retry")

    # ── Core pipeline ──────────────────────────────────────────────────────

    async def _process_config_data(self, payload: bytes) -> None:
        """Handle runtime configuration updates."""
        try:
            data = json.loads(payload.decode())
            if "vibration_enabled" in data:
                new_val = bool(data["vibration_enabled"])
                self._vibration_enabled = new_val
                logger.info(f"[CONFIG] Vibration enabled set to: {new_val}")
                
                # If disabled while active, stop vibration immediately
                if not new_val and self._alert_active:
                    cmd = ControlCommand(device_id="esp32-1", command="vibrate", active=False)
                    if _mqtt_ref:
                        _mqtt_ref.publish_control(cmd)
                    self._alert_active = False
                    self._alert_status = AlertStatus.IDLE
                    logger.info("[CONFIG] Vibration disabled - force stopping active alert")

            # ── Smoothing & Confidence config ────────────────────────────────
            if "min_confidence" in data:
                val = max(0.0, min(1.0, float(data["min_confidence"])))
                self._min_confidence = val
                self._local_db.set_config("min_confidence", str(val))
                logger.info(f"[CONFIG] min_confidence set to: {val:.0%}")

            if "smoothing_window_size" in data:
                val = max(1, int(data["smoothing_window_size"]))
                self._smoothing_window_size = val
                self._prediction_window = deque(self._prediction_window, maxlen=val)
                self._local_db.set_config("smoothing_window_size", str(val))
                logger.info(f"[CONFIG] smoothing_window_size set to: {val}")

            if "smoothing_min_votes" in data:
                val = max(1, int(data["smoothing_min_votes"]))
                self._smoothing_min_votes = val
                self._local_db.set_config("smoothing_min_votes", str(val))
                logger.info(f"[CONFIG] smoothing_min_votes set to: {val}")

        except Exception as e:
            logger.error(f"Failed to parse config message: {e}")

    async def _process_model_reload(self, payload: bytes) -> None:
        """
        Hot-reload AI model without restarting the Fog Node.

        Received from Launcher via MQTT topic: cushion/fog/model_reload
        Payload: {"model_path": "...", "scaler_path": "..."}

        The InferenceEngine.reload() swaps the model in-place and automatically
        restores the old model if loading fails, so inference is never interrupted.
        """
        try:
            data = json.loads(payload.decode())
            new_model_type  = data.get("model_type",  "keras").strip()
            new_model_path  = data.get("model_path",  "").strip()
            new_scaler_path = data.get("scaler_path", "").strip()

            if not new_model_path or (new_model_type == "keras" and not new_scaler_path):
                logger.error("[HOT-RELOAD] Missing model_path or scaler_path in payload")
                return

            mp = Path(new_model_path)
            sp = Path(new_scaler_path) if new_model_type == "keras" else None

            if not mp.exists():
                logger.error(f"[HOT-RELOAD] Model file not found: {mp}")
                return
            if sp and not sp.exists():
                logger.error(f"[HOT-RELOAD] Scaler file not found: {sp}")
                return

            logger.info(f"[HOT-RELOAD] Swapping [{new_model_type}] model → {mp.name}")
            print(f"🔥 [HOT-RELOAD] Swapping [{new_model_type}] model → {mp.name}")
            success = self._inference.reload(new_model_type, str(mp), str(sp) if sp else "")
            if not success:
                logger.warning("[HOT-RELOAD] Swap failed — previous model still active")
                print("⚠️ [HOT-RELOAD] Swap failed — previous model still active")

        except Exception as e:
            logger.error(f"[HOT-RELOAD] Unexpected error: {e}")

    async def _process_sensor_data(self, raw_bytes: bytes) -> None:
        """
        Full pipeline for one MQTT message from an ESP32.

        Steps:
          1. Parse JSON → RawMessage
          2. Merge partial readings → AggregatedSensorReading
          3. Extract raw FSR array (9 values, 0–4095)
          4. Run InferenceEngine → 11-label PostureLabel + confidence
          5. Derive OccupancyState from label
          6. Update session tracking (start/end, alert counter)
          7. Send vibration command to ESP32-1 if alert threshold reached (Interface 05)
          8. Broadcast FogRealtimeUpdate via WebSocket (Interface 02)
        """
        # Step 1 – Parse
        try:
            raw_dict = json.loads(raw_bytes)
            raw_msg  = RawMessage.model_validate(raw_dict)
        except Exception as exc:
            logger.warning(f"Invalid sensor message, skipping: {exc}")
            return

        # Step 2 – Merge partial readings
        updated = {k: v for k, v in raw_msg.sensors.model_dump().items() if v is not None}
        for k, v in updated.items():
            setattr(self._current_sensors, k, v)

        sensors = self._current_sensors

        # Step 3 – Extract raw FSR array
        raw_fsr = self._preprocessor.extract_raw(sensors)

        # Step 4 – AI inference (11 labels)
        posture_raw, confidence_raw, top_3 = self._inference.predict(raw_fsr)
        total_p = int(raw_fsr.sum())

        # Step 4a – Confidence filter: reject predictions below threshold
        if confidence_raw >= self._min_confidence:
            log_msg = (
                f"[AI] ✅ {posture_raw.value} | conf={confidence_raw:.2%} "
                f"(>={self._min_confidence:.0%}) | total_p={total_p}"
            )
            logger.info(log_msg)
            # Step 4b – Feed confident prediction into smoothing window
            self._prediction_window.append(posture_raw.value)
            posture = self._apply_smoothing()
        else:
            log_msg = (
                f"[AI] ⚠️  {posture_raw.value} | conf={confidence_raw:.2%} "
                f"(< threshold {self._min_confidence:.0%}) | total_p={total_p} "
                f"→ rejected, holding: {self._last_stable_posture.value}"
            )
            logger.info(log_msg)
            posture = self._last_stable_posture


        confidence = confidence_raw

        # Step 5 – Derive occupancy from label (no temperature logic)
        occupancy = occupancy_from_label(posture)

        # Step 6 – Session tracking (Pure AI, no temperature)
        person_is_sitting = posture in SITTING_POSTURES
        current_ts = time.time()

        if person_is_sitting:
            if self._session_start is None:
                # Session started
                self._session_start   = datetime.now(timezone.utc)
                self._session_id      = generate_session_id()
                self._alert_count     = 0
                self._consecutive_bad = 0
                self._alert_status    = AlertStatus.IDLE
                self._alert_active    = False
                self._session_manager.start_session(self._session_id, self._session_start, current_ts)
                logger.info(f"Session started: {self._session_id}")
                
                # Publish Event
                event = CloudEventRecord(
                    device_id=settings.device_id,
                    session_id=self._session_id,
                    fog_timestamp_iso=datetime.now(timezone.utc).isoformat(),
                    event_type=EventType.SESSION_STARTED,
                    occupancy_state=occupancy,
                )
                asyncio.create_task(self._cloud_sync.publish_event(event))

            self._session_manager.add_reading(posture, current_ts)

        if not person_is_sitting and self._session_start is not None:
            # Session ended
            end_time_iso = datetime.now(timezone.utc).isoformat()
            
            # Stop vibration immediately if active
            if self._alert_active:
                cmd = ControlCommand(device_id="esp32-1", command="vibrate", active=False)
                _get_mqtt_client().publish_control(cmd)
                logger.info("[ALERT] Stop vibration – session ended (user stood up)")

            # Publish Event
            event = CloudEventRecord(
                device_id=settings.device_id,
                session_id=self._session_id,
                fog_timestamp_iso=end_time_iso,
                event_type=EventType.SESSION_ENDED,
                occupancy_state=occupancy,
                posture=posture,
            )
            asyncio.create_task(self._cloud_sync.publish_event(event))
            
            # Publish Summary
            summary = self._session_manager.get_summary(settings.device_id, end_time_iso, self._alert_count)
            asyncio.create_task(self._cloud_sync.publish_summary(summary))
            
            logger.info(f"Session ended: {self._session_id}")
            self._session_start   = None
            self._session_id      = ""
            self._alert_count     = 0
            self._consecutive_bad = 0
            self._alert_status    = AlertStatus.IDLE
            self._alert_active    = False


        session_duration_sec = 0
        session_start_iso    = ""
        if self._session_start:
            session_duration_sec = int(
                (datetime.now(timezone.utc) - self._session_start).total_seconds()
            )
            session_start_iso = self._session_start.isoformat()

        # Step 7 – Alert logic (Pure AI-driven, no temperature check)
        is_bad_posture = person_is_sitting and (posture not in GOOD_POSTURES)
        
        # Override if vibration is disabled (runtime flag)
        if is_bad_posture and self._vibration_enabled:
            now_time = datetime.now(timezone.utc)
            # Re-send every 5s if still bad, or start for the first time
            if (not self._alert_active) or (hasattr(self, '_last_vibrate_time') and (now_time - self._last_vibrate_time).total_seconds() > 5):
                cmd = ControlCommand(
                    device_id="esp32-1",
                    command="vibrate",
                    active=True,
                    pattern="continuous",
                    intensity=200, # Default intensity
                )
                if _mqtt_ref:
                    _mqtt_ref.publish_control(cmd)
                self._last_vibrate_time = now_time
                
                if not self._alert_active:
                    self._alert_active = True
                    self._alert_count += 1
                    self._alert_status = AlertStatus.WARNING
                    logger.info(f"[ALERT] Start continuous vibration – posture: {posture.value}")
                else:
                    logger.debug("[ALERT] Re-sending vibration command (Keep-alive)")
                
                # Publish Event
                event = CloudEventRecord(
                    device_id=settings.device_id,
                    session_id=self._session_id,
                    fog_timestamp_iso=datetime.now(timezone.utc).isoformat(),
                    event_type=EventType.ALERT_TRIGGERED,
                    occupancy_state=occupancy,
                    posture=posture,
                )
                asyncio.create_task(self._cloud_sync.publish_event(event))
        else:
            if self._alert_active:
                # Stop vibration on transition to GOOD, EMPTY, or DISABLED
                cmd = ControlCommand(
                    device_id="esp32-1",
                    command="vibrate",
                    active=False,
                )
                _mqtt_ref.publish_control(cmd)
                self._alert_active = False
                self._alert_status = AlertStatus.IDLE
                logger.info("[ALERT] Stop vibration – posture corrected or disabled")

        # Step 8 – Broadcast Interface 02 via WebSocket
        broadcast = FogRealtimeUpdate(
            device_id=settings.device_id,
            session_id=self._session_id,
            session_start_time_iso=session_start_iso,
            occupancy_state=occupancy,
            posture=posture,
            posture_raw=posture_raw,
            confidence=confidence,
            temperature=self._preprocessor.get_temperature(sensors),
            alert_active=self._alert_active,
            alert_status=self._alert_status,
            alert_count=self._alert_count,
            session_duration_sec=session_duration_sec,
            poor_posture_duration_sec=self._session_manager.get_poor_posture_duration_sec(),
            good_posture_pct=self._session_manager.get_good_posture_pct(),
            posture_distribution=self._session_manager.get_posture_distribution(),
            sensors_heatmap_pct=sensors.as_heatmap_pct(),
            posture_top3=top_3,
            sensors=sensors.model_dump(),
        )
        payload = broadcast.model_dump()
        await self._ws_server.broadcast(payload)
        await self._cloud_ws_relay.send(payload)  # relay to AWS WebSocket (no-op if not configured)

        logger.debug(
            f"Pipeline done – posture={posture.value}, occupancy={occupancy.value}, "
            f"alert={self._alert_active}, ws_clients={self._ws_server.connected_count}"
        )

    # ── Smoothing ──────────────────────────────────────────────────────────

    def _apply_smoothing(self) -> PostureLabel:
        """
        Temporal smoothing via majority vote over a sliding window.

        Only predictions that pass the confidence filter are added to the
        window.  A posture is "confirmed" (stable) when one label reaches
        `_smoothing_min_votes` votes out of the last window entries.
        Until consensus is reached the previous stable posture is returned.
        """
        if not self._prediction_window:
            return self._last_stable_posture

        counts              = Counter(self._prediction_window)
        top_label_str, top_count = counts.most_common(1)[0]
        win_size            = len(self._prediction_window)

        # Map string label back to PostureLabel enum
        try:
            top_label = PostureLabel(top_label_str)
        except ValueError:
            return self._last_stable_posture

        if top_count >= self._smoothing_min_votes:
            if top_label != self._last_stable_posture:
                logger.info(
                    f"[SMOOTH] ✅ Confirmed: {top_label.value} "
                    f"({top_count}/{win_size} votes)"
                )
            self._last_stable_posture    = top_label
            self._last_stable_confidence = top_count / win_size
        else:
            logger.debug(
                f"[SMOOTH] ⏳ {top_label.value} {top_count}/{self._smoothing_min_votes} votes "
                f"→ holding {self._last_stable_posture.value}"
            )

        return self._last_stable_posture

    # ── Shutdown ───────────────────────────────────────────────────────────

    def _request_shutdown(self) -> None:
        logger.info("Shutdown signal received – stopping Fog Node...")
        self._running = False

    # ── Banner ─────────────────────────────────────────────────────────────

    def _print_banner(self) -> None:
        border = "=" * 58
        logger.info(border)
        logger.info("  Smart Cushion Fog Node")
        logger.info(f"  MQTT Broker  : {settings.mqtt_host}:{settings.mqtt_port}")
        logger.info(f"  WebSocket    : ws://{settings.ws_host}:{settings.ws_port}")
        logger.info(f"  AI Model     : {settings.model_path}")
        logger.info(f"  Scaler       : {settings.scaler_path}")
        logger.info(f"  Cloud Sync   : {'ENABLED' if settings.cloud_enabled else 'disabled'}")
        logger.info(f"  Min Conf.    : {self._min_confidence:.0%}  |  "
                    f"Window: {self._smoothing_window_size} frames "
                    f"(min {self._smoothing_min_votes} votes to confirm)")
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
