"""
Data schemas for all messages flowing through the Smart Cushion system.

Every message entering or leaving the Fog Node is validated against
these Pydantic v2 models, catching malformed payloads early and
providing clear error messages during development.

Message flow:
  ESP32 -> MQTT(cushion/raw)  -> RawMessage
  Fog   -> MQTT(cushion/ctrl) -> ControlCommand
  Fog   -> WebSocket          -> WebSocketBroadcast
  Fog   -> MQTT(cushion/sync) -> CloudSyncPayload (AWS IoT Core)
"""

from enum import Enum
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PostureLabel(str, Enum):
    """All posture states the AI engine can detect."""
    CORRECT         = "correct"
    LEAN_LEFT       = "lean_left"
    LEAN_RIGHT      = "lean_right"
    SLOUCH_FORWARD  = "slouch_forward"
    LEAN_BACK       = "lean_back"
    UNKNOWN         = "unknown"   # Used when no person is detected


# ---------------------------------------------------------------------------
# Edge -> Fog (MQTT cushion/raw)
# ---------------------------------------------------------------------------

class SensorReading(BaseModel):
    """
    Raw sensor values from the ESP32.

    FSR sensors return 12-bit ADC values (0–4095 on ESP32).
    We cap at 4095 to be flexible, but default ESP32 analogRead is 12-bit.
    Temperature is from an IR sensor (MLX90614 or similar).
    """
    fsr_top_left:     int   = Field(..., ge=0, le=4095, description="FSR top-left ADC value")
    fsr_top_right:    int   = Field(..., ge=0, le=4095, description="FSR top-right ADC value")
    fsr_bottom_left:  int   = Field(..., ge=0, le=4095, description="FSR bottom-left ADC value")
    fsr_bottom_right: int   = Field(..., ge=0, le=4095, description="FSR bottom-right ADC value")
    temperature:      float = Field(..., ge=-40.0, le=125.0, description="Object temperature °C")


class RawMessage(BaseModel):
    """
    Full JSON payload published by the ESP32 to MQTT topic cushion/raw.

    Example:
        {
            "device_id":  "esp32-cushion-01",
            "timestamp":  1712345678.123,
            "sensors": {
                "fsr_top_left":     512,
                "fsr_top_right":    498,
                "fsr_bottom_left":  601,
                "fsr_bottom_right": 587,
                "temperature":      36.4
            }
        }
    """
    device_id: str
    timestamp: float = Field(..., description="Unix timestamp (seconds) from ESP32")
    sensors:   SensorReading


# ---------------------------------------------------------------------------
# Fog -> Edge (MQTT cushion/control)
# ---------------------------------------------------------------------------

class ControlCommand(BaseModel):
    """
    Command sent from Fog Node back to the ESP32 to trigger the vibration motor.

    The ESP32 subscribes to MQTT topic cushion/control and activates the motor
    for `duration_ms` milliseconds upon receiving this message.
    """
    command:     str = Field(default="vibrate", description="Action for the ESP32 to execute")
    duration_ms: int = Field(default=1000, ge=100, le=10000, description="Vibration duration (ms)")
    reason:      str = Field(default="",   description="The detected posture that triggered the alert")


# ---------------------------------------------------------------------------
# Fog -> Web App (WebSocket)
# ---------------------------------------------------------------------------

class WebSocketBroadcast(BaseModel):
    """
    Real-time posture data streamed to connected Web App clients via WebSocket.
    Sent after every sensor reading is processed by the AI engine.
    """
    timestamp:       float
    posture:         PostureLabel
    confidence:      float        = Field(..., ge=0.0, le=1.0)
    person_detected: bool
    sensors:         SensorReading
    alert_sent:      bool         = Field(description="True if vibration was triggered this cycle")


# ---------------------------------------------------------------------------
# Fog -> Cloud (MQTT cushion/sync to AWS IoT Core)
# ---------------------------------------------------------------------------

class PostureCounts(BaseModel):
    """Per-posture reading counts within a sync window."""
    correct:        int = 0
    lean_left:      int = 0
    lean_right:     int = 0
    slouch_forward: int = 0
    lean_back:      int = 0


class CloudSyncPayload(BaseModel):
    """
    Session summary published to AWS IoT Core every CLOUD_SYNC_INTERVAL seconds.

    This is the only data that leaves the local network, ensuring user
    privacy while still enabling cloud-based historical analytics.

    Example:
        {
            "device_id":         "esp32-cushion-01",
            "window_start":      1712345600.0,
            "window_end":        1712345660.0,
            "correct_seconds":   45.0,
            "incorrect_seconds": 15.0,
            "posture_counts": {
                "correct": 9, "lean_left": 2, "lean_right": 1,
                "slouch_forward": 1, "lean_back": 0
            }
        }
    """
    device_id:          str
    window_start:       float
    window_end:         float
    correct_seconds:    float = Field(..., ge=0.0)
    incorrect_seconds:  float = Field(..., ge=0.0)
    posture_counts:     PostureCounts
