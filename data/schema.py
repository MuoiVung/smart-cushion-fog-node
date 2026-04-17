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
    Partial raw sensor values from the ESP32 hardware.
    Since sensors are split between 2 ESP32s, fields are optional.
    
    FSR sensors (FSR402 - Interlink Electronics):
      - Return raw 12-bit ADC values via ESP32 analogRead() (range: 0–4095).
    """
    fsr_front_left:  int | None = Field(default=None, ge=0, le=4095)
    fsr_front_mid:   int | None = Field(default=None, ge=0, le=4095)
    fsr_front_right: int | None = Field(default=None, ge=0, le=4095)
    fsr_mid_left:    int | None = Field(default=None, ge=0, le=4095)
    fsr_mid_mid:     int | None = Field(default=None, ge=0, le=4095)
    fsr_mid_right:   int | None = Field(default=None, ge=0, le=4095)
    fsr_back_left:   int | None = Field(default=None, ge=0, le=4095)
    fsr_back_mid:    int | None = Field(default=None, ge=0, le=4095)
    fsr_back_right:  int | None = Field(default=None, ge=0, le=4095)
    temperature:     float | None = Field(default=None, ge=-40.0, le=125.0)

class AggregatedSensorReading(BaseModel):
    """Aggregated state from all 9 FSR sensors + temperature."""
    fsr_front_left:  int = Field(default=0, ge=0, le=4095)
    fsr_front_mid:   int = Field(default=0, ge=0, le=4095)
    fsr_front_right: int = Field(default=0, ge=0, le=4095)
    fsr_mid_left:    int = Field(default=0, ge=0, le=4095)
    fsr_mid_mid:     int = Field(default=0, ge=0, le=4095)
    fsr_mid_right:   int = Field(default=0, ge=0, le=4095)
    fsr_back_left:   int = Field(default=0, ge=0, le=4095)
    fsr_back_mid:    int = Field(default=0, ge=0, le=4095)
    fsr_back_right:  int = Field(default=0, ge=0, le=4095)
    temperature:     float = Field(default=25.0, ge=-40.0, le=125.0)


class RawMessage(BaseModel):
    """
    Full JSON payload published by the ESP32 to MQTT topic cushion/raw.

    Real hardware example (FSR402 + NTC thermistor, person NOT seated):
        {
            "device_id":  "esp32-cushion-01",
            "timestamp":  115.265,
            "sensors": {
                "fsr_front_left":  2788,
                "fsr_front_right": 3052,
                "fsr_back_left":   2590,
                "fsr_back_right":  2428,
                "temperature":     20.4
            }
        }

    Notes:
      - timestamp: seconds since ESP32 boot (millis()/1000), NOT Unix epoch.
      - temperature 20.4 °C → below 30 °C threshold → person_detected = False.
      - FSR values ~2400–3100 at rest (no weight) due to sensor baseline offset.
    """
    device_id: str
    timestamp: float = Field(..., description="Seconds since ESP32 boot (millis()/1000)")
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
    sensors:         AggregatedSensorReading
    features:        list[float] | None = Field(default=None, description="Processed AI features (0.0 to 1.0)")
    alert_sent:      bool         = Field(description="True if vibration was triggered this cycle")
    trigger_device_id: str        = Field(default="unknown", description="Which ESP32 triggered this broadcast")


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
