# ⚙️ Smart Cushion Fog Node

### Ultra-Low Latency Local AI Broker & Inference Engine

The "Brain" of the Smart Cushion system. This node operates locally on a PC or Raspberry Pi to aggregate raw sensor data, perform instant AI posture classification, and act as a reliable bridge to the Cloud.

<p align="center">
  <b>Keras CNN Inference ｜ Mosquitto Broker ｜ AWS IoT Bridging ｜ SQLite Caching</b>
</p>

---

## 🔗 Project Links

| Item                    | Link                                                                                          |
| ----------------------- | --------------------------------------------------------------------------------------------- |
| 🌐 Project Website      | [https://tonguyentanphuong.github.io/smart-cushion-web/](https://github.com/tonguyentanphuong/smart-cushion-web) |
| ⚙️ Fog Repository       | [https://github.com/MuoiVung/smart-cushion-fog-node](https://github.com/MuoiVung/smart-cushion-fog-node) |
| 📦 Edge Hardware Repo   | [https://github.com/MuoiVung/smart-cushion-edge](https://github.com/MuoiVung/smart-cushion-edge) |
| 🧠 AI Training Repo     | [https://github.com/MuoiVung/smart-cushion-AI](https://github.com/MuoiVung/smart-cushion-AI)     |
| ⚙️ Main Architecture    | [https://github.com/MuoiVung/smart-cushion](https://github.com/MuoiVung/smart-cushion)            |

---

## 📌 Project Overview

The Fog Node exists to solve the fundamental latency and privacy issues associated with Cloud AI. 

Sending continuous raw analog sensor data to the AWS cloud for real-time inference introduces noticeable network delay and incurs massive API costs. Instead, the Fog Node runs a local **Keras Convolutional Neural Network (CNN)**. It receives 9 ADC values from the ESP32 via local MQTT, normalizes them, and predicts 1 of 9 postures in **under 50ms**. 

It also manages local state logic: if a bad posture is sustained, it sends an immediate vibration command back to the edge. When the user stands up, it aggregates the entire session and uploads a lightweight summary to AWS IoT Core.

This project includes:
* 🧠 **AI Engine** — Hot-reloadable Keras inference pipeline.
* 📡 **MQTT Brokerage** — Local Eclipse Mosquitto management.
* 💾 **Offline Queuing** — SQLite buffering for when internet connectivity drops.
* 🖥️ **Launcher GUI** — Tkinter interface for easy model swapping.

---

## 🛠️ Technology Stack

| Layer              | Tools / Components                             |
| ------------------ | ---------------------------------------------- |
| Core Language      | Python 3.9+                                    |
| AI Engine          | TensorFlow / Keras, Scikit-Learn (`.pkl` scaler) |
| Local Broker       | Eclipse Mosquitto (MQTT)                       |
| Database           | SQLite (`fog_local.db` for offline queuing)    |
| WebSockets         | `websockets` / `asyncio`                       |
| Cloud Integration  | AWS IoT Core (boto3, AWSIoTPythonSDK)          |
| Containerization   | Docker, Docker Compose                         |

---

## 💡 Motivation

Why use a Fog Node instead of sending data directly from the ESP32 to AWS?

| Problem                      | Cloud-Only Solution | Fog-Enabled Solution (Ours) |
| ---------------------------- | ------------------- | --------------------------- |
| **Latency**                  | ~500ms delay over Wi-Fi/4G | **<50ms delay** on local network |
| **Privacy**                  | Raw weight distributions stored online | Only high-level summaries go online |
| **Cost**                     | Paying AWS for 10 requests per second | Paying AWS for 1 request per session |
| **Offline Reliability**      | Vibration motor fails if Wi-Fi drops | Local inference continues working offline |

---

## 🧩 System Architecture (Fog)

### Internal Workflow

| Phase       | Action                                                       |
| ----------- | --------------------------------------------------------------- |
| **1. Ingestion**| `mqtt_handler.py` receives UDP/MQTT payloads from the ESP32. |
| **2. Scaling**  | Raw values (0-4095) are scaled to (0.0-1.0) using Scikit-Learn. |
| **3. Inference**| The Keras `.h5` model classifies the 3x3 matrix as an "image" into 9 states. |
| **4. Feedback** | If state == "Bad Posture" for >30s, publish vibration payload to `cushion/command`. |
| **5. Broadcast**| `websocket_server.py` pushes real-time state to the local React App. |
| **6. Upload**   | When the session ends, `aws_iot_handler.py` pushes a summary to AWS via TLS. |

---

## 🗂️ Repository Structure

```text
smart-cushion-fog-node/
│
├── README.md
├── .env.example
├── requirements.txt
├── docker-compose.yml       (Optional containerized deployment)
│
├── app.py                   (Main event loop and initialization)
├── run_launcher.py          (Tkinter GUI for easy launching and model selection)
│
├── core/
│   ├── mqtt_handler.py      (Local ESP32 communication)
│   └── aws_iot_handler.py   (AWS IoT Cloud communication)
├── ai/                      (Inference engine and model loading)
├── config/                  (Environment and SQLite DB setup)
├── data/                    (Local SQLite storage for offline buffering)
└── models/                  (Directory for .h5 weights and .pkl scalers)
```

---

## 🚀 Deployment Guide

### 1. Prerequisites
- **Python 3.9+** installed.
- **Mosquitto MQTT Broker**: 
  - Windows: [mosquitto.org](https://mosquitto.org/download/)
  - Mac: `brew install mosquitto`

### 2. Environment Setup
1. Clone the repo and create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
2. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and configure your IPs. Ensure `MQTT_BROKER_IP` is `localhost`.

### 3. Running the Fog Node
1. Ensure the Mosquitto broker is running in the background.
2. Launch the GUI:
   ```bash
   python run_launcher.py
   ```
3. Select your desired `.h5` model and `.pkl` scaler from the dropdown.
4. Click **Start Fog Engine**.

---

## 👥 Team Members & Roles

| Member | Role                                  | Responsibility                                                                                                                                               |
| ------ | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Tran Viet Nam** | Fog & Hardware Integration | Architecting the Fog node broker, MQTT communication, local SQLite caching, system integration, and bridging hardware with cloud services. |

---

## 🎯 Conclusion
The Fog Layer guarantees a real-time, responsive user experience. By caching events locally in SQLite during internet outages and processing complex AI locally, the system ensures reliable haptic feedback regardless of cloud connectivity, protecting user privacy while drastically reducing cloud computing costs.
