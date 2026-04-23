"""
Data schemas for all messages flowing through the Smart Cushion system.

Every message entering or leaving the Fog Node is validated against
these Pydantic v2 models, catching malformed payloads early and
providing clear error messages during development.

Message flow (ref: system_architecture.md):
  ESP32 → MQTT(cushion/raw)     → RawMessage
  Fog   → MQTT(cushion/command) → ControlCommand
  Fog   → WebSocket             → FogRealtimeUpdate      (Interface 02)
  Fog   → MQTT(cushion/{id}/event)     → CloudEventRecord
  Fog   → MQTT(cushion/{id}/telemetry) → CloudTelemetryRecord
  Fog   → MQTT(cushion/{id}/summary)   → CloudSummaryRecord
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PostureLabel(str, Enum):
    """
    11 AI output labels (system_architecture.md §1).

    Surface state (2 labels):
      empty   – no person or object on the cushion
      object  – something placed on cushion, but NOT a seated person

    Posture labels (9 labels, person is seated):
      NUP, LF, LB, LFSR, LFSL, CRL, CLL, CRLL, CLLL
    """
    # Surface state labels
    EMPTY  = "EMPTY"   # Nothing on cushion
    OBJECT = "OBJECT"  # Object detected, not a person

    # Posture labels (person is seated)
    NUP  = "NUP"   # Natural Upright Posture – correct posture
    LF   = "LF"    # Lean Forward
    LB   = "LB"    # Lean Backward
    LFSR = "LFSR"  # Lean Forward-Support Right
    LFSL = "LFSL"  # Lean Forward-Support Left
    CRL  = "CRL"   # Cross-Right Legged
    CLL  = "CLL"   # Cross-Left Legged
    CRLL = "CRLL"  # Cross-Right Legged-Legged
    CLLL = "CLLL"  # Cross-Left Legged-Legged


# Only NUP is a correct seated posture (no vibration alert)
GOOD_POSTURES: frozenset[PostureLabel] = frozenset({PostureLabel.NUP})

# Labels that indicate a human is actually seated (for session tracking)
SITTING_POSTURES: frozenset[PostureLabel] = frozenset({
    PostureLabel.NUP,  PostureLabel.LF,   PostureLabel.LB,
    PostureLabel.LFSR, PostureLabel.LFSL, PostureLabel.CRL,
    PostureLabel.CLL,  PostureLabel.CRLL, PostureLabel.CLLL,
})

# Map each AI label → OccupancyState (defined below, forward ref)
def occupancy_from_label(label: "PostureLabel") -> "OccupancyState":
    if label == PostureLabel.EMPTY:
        return OccupancyState.EMPTY
    if label == PostureLabel.OBJECT:
        return OccupancyState.UNCERTAIN
    return OccupancyState.OCCUPIED


class OccupancyState(str, Enum):
    OCCUPIED  = "occupied"
    EMPTY     = "empty"
    UNCERTAIN = "uncertain"


class AlertStatus(str, Enum):
    IDLE     = "IDLE"
    WARNING  = "WARNING"
    COOLDOWN = "COOLDOWN"


class EventType(str, Enum):
    ALERT_TRIGGERED = "alert_triggered"
    SESSION_STARTED = "session_started"
    SESSION_ENDED   = "session_ended"


# ---------------------------------------------------------------------------
# Edge → Fog  (MQTT  cushion/raw)  — Interface 01
# ---------------------------------------------------------------------------

class SensorReading(BaseModel):
    """
    Partial raw sensor values from one ESP32.
    Fields are Optional because the 9 FSRs are split across two boards.
    All ADC values are 12-bit (0–4095).
    """
    # ESP32-1: 4 corner FSRs + temperature
    fsr_front_left:  Optional[int]   = Field(default=None, ge=0, le=4095)
    fsr_front_right: Optional[int]   = Field(default=None, ge=0, le=4095)
    fsr_back_left:   Optional[int]   = Field(default=None, ge=0, le=4095)
    fsr_back_right:  Optional[int]   = Field(default=None, ge=0, le=4095)
    temperature:     Optional[float] = Field(default=None, ge=0.0, le=100.0)
    # ESP32-2: 5 inner FSRs
    fsr_front_mid:   Optional[int]   = Field(default=None, ge=0, le=4095)
    fsr_mid_mid:     Optional[int]   = Field(default=None, ge=0, le=4095)
    fsr_back_mid:    Optional[int]   = Field(default=None, ge=0, le=4095)
    fsr_mid_left:    Optional[int]   = Field(default=None, ge=0, le=4095)
    fsr_mid_right:   Optional[int]   = Field(default=None, ge=0, le=4095)


class ActuatorState(BaseModel):
    """Actuator state reported by ESP32-1."""
    vibration_status: bool = False


class RawMessage(BaseModel):
    """
    Full JSON payload published by an ESP32 to MQTT topic cushion/raw.
    Matches system_architecture.md §2.1 Interface 01.
    """
    device_id: str
    timestamp: float = Field(..., description="Seconds since ESP32 boot (millis()/1000)")
    sensors:   SensorReading
    actuator:  Optional[ActuatorState] = None   # Only present in ESP32-1 payloads


class AggregatedSensorReading(BaseModel):
    """
    Merged state of all 9 FSR sensors + temperature.
    The Fog updates this incrementally as messages arrive from each ESP32.
    """
    fsr_front_left:  int   = Field(default=0, ge=0, le=4095)
    fsr_front_right: int   = Field(default=0, ge=0, le=4095)
    fsr_back_left:   int   = Field(default=0, ge=0, le=4095)
    fsr_back_right:  int   = Field(default=0, ge=0, le=4095)
    fsr_front_mid:   int   = Field(default=0, ge=0, le=4095)
    fsr_mid_mid:     int   = Field(default=0, ge=0, le=4095)
    fsr_back_mid:    int   = Field(default=0, ge=0, le=4095)
    fsr_mid_left:    int   = Field(default=0, ge=0, le=4095)
    fsr_mid_right:   int   = Field(default=0, ge=0, le=4095)
    temperature:     float = Field(default=25.0, ge=0.0, le=100.0)

    def as_heatmap_pct(self) -> list[float]:
        """
        Returns 9 FSR values as percentage (0–100) in the order used by
        sensors_heatmap_pct in the WebSocket broadcast (row-major, left→right,
        top→bottom):
          [FL, FM, FR, ML, MM, MR, BL, BM, BR]
        """
        keys = [
            "fsr_front_left", "fsr_front_mid",  "fsr_front_right",
            "fsr_mid_left",   "fsr_mid_mid",    "fsr_mid_right",
            "fsr_back_left",  "fsr_back_mid",   "fsr_back_right",
        ]
        return [round(getattr(self, k) / 4095 * 100, 1) for k in keys]


# ---------------------------------------------------------------------------
# Fog → Edge  (MQTT  cushion/command)  — Interface 05
# ---------------------------------------------------------------------------

class ControlCommand(BaseModel):
    """
    Command sent from the Fog Node to ESP32-1 to control the vibration motor.
    Matches system_architecture.md §2.2 Interface 05.
    """
    device_id: str    = Field(default="esp32-1")
    command:   str    = Field(default="vibrate")
    active:    bool   = Field(default=True)
    pattern:   Optional[str] = Field(default="short_triple",
                                     description="short_triple | long_single")
    intensity: int    = Field(default=200, ge=0, le=255,
                              description="PWM duty cycle (0–255)")


# ---------------------------------------------------------------------------
# Fog → Local App  (WebSocket)  — Interface 02
# ---------------------------------------------------------------------------

def _new_session_id() -> str:
    now = datetime.now(timezone.utc)
    return f"sess-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"


class FogRealtimeUpdate(BaseModel):
    """
    Real-time update broadcast to the local app via WebSocket every 0.5 s.
    Matches system_architecture.md §2.3 Interface 02.
    Field names are 100% in sync with cloud schemas.
    """
    record_type:            str            = "realtime_update"
    device_id:              str            = Field(default="cushion-01")
    session_id:             str            = Field(default_factory=_new_session_id)
    session_start_time_iso: str            = Field(default="")
    occupancy_state:        OccupancyState = OccupancyState.EMPTY
    posture:                PostureLabel   = PostureLabel.EMPTY
    temperature:            float          = Field(default=0.0, ge=0.0, le=100.0)
    alert_active:           bool           = False
    alert_status:           AlertStatus    = AlertStatus.IDLE
    alert_count:            int            = Field(default=0, ge=0)
    session_duration_sec:   int            = Field(default=0, ge=0)
    sensors_heatmap_pct:    list[float]    = Field(default_factory=lambda: [0.0] * 9)


# ---------------------------------------------------------------------------
# Fog → Cloud  (AWS IoT Core MQTT)  — Interface 03
# ---------------------------------------------------------------------------

def _new_record_id(prefix: str) -> str:
    now = datetime.now(timezone.utc)
    return f"{prefix}-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:5].upper()}"


class CloudEventRecord(BaseModel):
    """
    Event Record – published on cushion/{device_id}/event.
    Sent on significant state changes (alert, session start/end).
    """
    record_type:      str       = "event_record"
    record_id:        str       = Field(default_factory=lambda: _new_record_id("evt"))
    device_id:        str       = Field(default="cushion-01")
    session_id:       str       = ""
    fog_timestamp_iso: str      = Field(default="")
    event_type:       EventType = EventType.ALERT_TRIGGERED
    occupancy_state:  OccupancyState = OccupancyState.OCCUPIED
    posture:          PostureLabel   = PostureLabel.EMPTY


class CloudTelemetryRecord(BaseModel):
    """
    Telemetry Record – published on cushion/{device_id}/telemetry.
    Sent periodically (e.g., every 30 s) while session is active.
    """
    record_type:       str            = "telemetry_record"
    record_id:         str            = Field(default_factory=lambda: _new_record_id("tel"))
    device_id:         str            = Field(default="cushion-01")
    session_id:        str            = ""
    fog_timestamp_iso: str            = Field(default="")
    occupancy_state:   OccupancyState = OccupancyState.OCCUPIED
    posture:           PostureLabel   = PostureLabel.EMPTY
    alert_active:      bool           = False


class PostureDurationBreakdown(BaseModel):
    """Per-posture accumulated duration in seconds for a session summary."""
    nup_duration_sec:  int = 0
    lf_duration_sec:   int = 0
    lb_duration_sec:   int = 0
    lfsr_duration_sec: int = 0
    lfsl_duration_sec: int = 0
    crl_duration_sec:  int = 0
    cll_duration_sec:  int = 0
    crll_duration_sec: int = 0
    clll_duration_sec: int = 0


class CloudSummaryRecord(BaseModel):
    """
    Summary Record – published on cushion/{device_id}/summary.
    Sent when a sitting session ends.
    """
    record_type:                 str    = "summary_record"
    record_id:                   str    = Field(default_factory=lambda: _new_record_id("sum"))
    device_id:                   str    = Field(default="cushion-01")
    session_id:                  str    = ""
    fog_timestamp_iso:           str    = Field(default="")
    start_time:                  str    = ""   # ISO 8601 UTC
    end_time:                    str    = ""   # ISO 8601 UTC
    total_sitting_duration_sec:  int    = Field(default=0, ge=0)
    poor_posture_duration_sec:   int    = Field(default=0, ge=0)
    alert_count:                 int    = Field(default=0, ge=0)
    posture_duration_breakdown:  PostureDurationBreakdown = Field(
        default_factory=PostureDurationBreakdown
    )


# ---------------------------------------------------------------------------
# Backwards-compatible alias (used in existing cloud_sync.py)
# ---------------------------------------------------------------------------
CloudSyncPayload = CloudSummaryRecord
