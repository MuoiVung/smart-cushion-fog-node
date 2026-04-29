# ErgoVita - Data Structures & Interfaces

This document specifies the detailed JSON formats for all communication flows within the Smart Cushion system.

---

## 1. Fog Node ↔ Cloud (AWS IoT Core)
**Purpose:** Sync session summaries, significant events, and periodic telemetry for historical tracking.

### 1.1. Session Summary
*   **Trigger:** Sent once when a sitting session ends (user stands up).
*   **Topic:** `cushion/{device_id}/summary`
*   **JSON Format:**
```json
{
  "record_type": "summary_record",
  "record_id": "sum-20260429-A1B2C",
  "device_id": "cushion-01",
  "session_id": "sess-20260429-XYZ9",
  "fog_timestamp_iso": "2026-04-29T15:20:00Z",
  "start_time": "2026-04-29T14:00:00Z",
  "end_time": "2026-04-29T15:20:00Z",
  "total_sitting_duration_sec": 4800,
  "poor_posture_duration_sec": 1200,
  "alert_count": 5,
  "posture_duration_breakdown": {
    "nup_duration_sec": 3600,
    "lf_duration_sec": 600,
    "lb_duration_sec": 0,
    "lfsr_duration_sec": 300,
    "lfsl_duration_sec": 100,
    "crl_duration_sec": 200,
    "cll_duration_sec": 0,
    "crll_duration_sec": 0,
    "clll_duration_sec": 0
  }
}
```

---

## 2. Fog Node ↔ ESP32 (Local MQTT)
**Purpose:** Collect raw sensor data and control the haptic feedback (vibration).

### 2.1. Fog → Edge (Vibration Control)
*   **Topic:** `cushion/command`
*   **Description:** Triggered by the Fog Node logic when a persistent poor posture is detected.
*   **JSON Format:**
```json
{
  "device_id": "esp32-1",
  "command": "vibrate",
  "active": true,
  "intensity": 255
}
```
*   `active`: `true` to start/pulsate vibration, `false` to stop.
*   `intensity`: 0-255 (PWM duty cycle controlling vibration strength).

### 2.2. Edge → Fog (Raw Telemetry)
*   **Topic:** `cushion/raw`
*   **JSON Format (ESP32-1 Example):**
```json
{
  "device_id": "esp32-1",
  "timestamp": 123.45,
  "sensors": {
    "fsr_front_left": 2800, "fsr_front_right": 3050,
    "fsr_back_left": 2590, "fsr_back_right": 2400,
    "temperature": 36.5
  },
  "actuator": { "vibration_status": false }
}
```

---

## 3. Fog Node ↔ Local App (WebSocket)
**Purpose:** Provide real-time data for the user dashboard.

*   **Frequency:** ~0.5 seconds.
*   **JSON Format:**
```json
{
  "record_type": "realtime_update",
  "device_id": "cushion-01",
  "session_id": "sess-20260429-XXXX",
  "session_start_time_iso": "2026-04-29T10:15:00Z",
  "occupancy_state": "occupied",
  "posture": "NUP",
  "temperature": 36.5,
  "alert_active": false,
  "alert_status": "IDLE",
  "alert_count": 5,
  "session_duration_sec": 411,
  "sensors_heatmap_pct": [85, 90, 40, 20, 15, 10, 5, 0, 0]
}
```
*   `sensors_heatmap_pct`: 9 pressure values as percentages, ordered: [FL, FM, FR, ML, MM, MR, BL, BM, BR].

---

## 4. AI Posture Labels
Standardized labels used across Cloud and App layers:

| Label | Description | Alert Trigger |
| :--- | :--- | :--- |
| **EMPTY** | No user detected | No |
| **NUP** | Natural Upright Posture (Correct) | No |
| **LF** | Lean Forward | Yes |
| **LB** | Lean Backward | Yes |
| **LFSR** | Lean Forward-Support Right | Yes |
| **LFSL** | Lean Forward-Support Left | Yes |
| **CRL / CLL** | Cross-Legged (Right/Left) | Yes |
| **CRLL / CLLL** | Deep Cross-Legged | Yes |
