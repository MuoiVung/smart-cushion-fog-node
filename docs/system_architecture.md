# ErgoVita - Smart Cushion System Architecture

This document describes the high-level architecture, AI classification logic, and detailed data flows for the ErgoVita project.

---

## 1. AI Posture Classification

The AI model identifies **11 labels** based on 9 FSR (Force Sensitive Resistor) sensor values arranged in a 3x3 matrix. 

### 1.1. Surface States (2 labels)

| Label | Name | Description |
| :--- | :--- | :--- |
| **EMPTY** | Empty Cushion | No user or object on the cushion. Very low total pressure. |
| **OBJECT** | Object Detected | Non-human object (e.g., bag, laptop) detected. Pressure distribution is irregular. |

### 1.2. Human Postures (9 labels)

Activated when the system detects a human is seated.

| Label | Name | Key Characteristic |
| :--- | :--- | :--- |
| **NUP** | Natural Upright Posture | Spine is naturally straight, weight is balanced. |
| **LF** | Lean Forward | Body leaning towards the front. |
| **LB** | Lean Backward | Body leaning towards the back. |
| **LFSR** | Lean Fwd-Support Right | Leaning forward with right arm support. |
| **LFSL** | Lean Fwd-Support Left | Leaning forward with left arm support. |
| **CRL** | Cross-Right Legged | Ankle of right leg over left knee. |
| **CLL** | Cross-Left Legged | Ankle of left leg over right knee. |
| **CRLL** | Cross-Right Legged-Legged | Deep cross-legged (right thigh over left). |
| **CLLL** | Cross-Left Legged-Legged | Deep cross-legged (left thigh over right). |

### 1.3. Business Logic Rules

| AI Label | Occupancy State | Haptic Alert |
| :--- | :--- | :--- |
| `EMPTY` | `empty` | No |
| `OBJECT` | `uncertain` | No |
| `NUP` | `occupied` | No (Correct posture) |
| Others | `occupied` | Yes (Triggered after N consecutive detections) |

---

## 2. Data Flow Summary

### 2.1. Edge to Fog (Raw Sensor Data)
*   **Protocol:** MQTT
*   **Topic:** `cushion/raw`
*   **Frequency:** 0.5s
*   **Content:** 9 FSR values + temperature + motor feedback.

### 2.2. Fog to Edge (Vibration Commands)
*   **Protocol:** MQTT
*   **Topic:** `cushion/command`
*   **Trigger:** Automated by Fog Node when poor posture persists.

### 2.3. Fog to Local App (Real-time Stream)
*   **Protocol:** WebSocket
*   **Frequency:** 0.5s
*   **Content:** Heatmap percentages, active posture, alert status, and session duration.

### 2.4. Fog to Cloud (Session Sync)
*   **Protocol:** MQTT over TLS (AWS IoT Core)
*   **Content:** Anonymized session summaries (start/end times, posture breakdown).

---

## 3. System Hardware Mapping

The 9 FSR sensors are mapped to a 3x3 grid as follows:

| FL (Front Left) | FM (Front Mid) | FR (Front Right) |
| :--- | :--- | :--- |
| **ML (Mid Left)** | **MM (Mid Mid)** | **MR (Mid Right)** |
| **BL (Back Left)** | **BM (Back Mid)** | **BR (Back Right)** |

*   **ESP32-1** handles: FL, FR, BL, BR + Temperature + Vibration.
*   **ESP32-2** handles: FM, MM, BM, ML, MR.
