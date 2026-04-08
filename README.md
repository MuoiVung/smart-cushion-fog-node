# 🪑 Smart Cushion – Fog Node

> **The local AI processing hub** of the Smart Cushion AIoT system.
> Receives raw sensor data from an ESP32 over MQTT, classifies posture with a
> PyTorch model, streams live results to a Web App via WebSocket, and
> periodically syncs session summaries to AWS IoT Core.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Project Structure](#project-structure)
3. [Data Formats](#data-formats)
4. [Prerequisites](#prerequisites)
5. [Quick Start (Docker)](#quick-start-docker)
6. [Native Setup (no Docker)](#native-setup-no-docker)
7. [Configuration & Security](#configuration--security)
8. [Running Tests](#running-tests)
9. [Testing with the ESP32 Simulator](#testing-with-the-esp32-simulator)
10. [WebSocket Demo Client](#websocket-demo-client)
11. [Adding Your Trained PyTorch Model](#adding-your-trained-pytorch-model)
12. [Cloud Sync (AWS IoT Core)](#cloud-sync-aws-iot-core)
13. [Architecture Advice](#architecture-advice)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                     EDGE (ESP32 Firmware)                            │
│  FSR×4 + IR Temp → JSON → MQTT(cushion/raw)                         │
│  MQTT(cushion/control) ← Vibration motor command                     │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ Wi-Fi / MQTT (local network)
┌──────────────────────────▼───────────────────────────────────────────┐
│                     FOG NODE (this repository)                       │
│                                                                      │
│  MQTTClient ──► Preprocessor ──► InferenceEngine (PyTorch)          │
│      │                                    │                          │
│      │                           SessionManager                      │
│      │                                    │                          │
│      ◄──── cushion/control ─────── alert?  │                         │
│                                            │                         │
│                             WebSocketServer (ws://:8765)             │
│                             CloudSync → AWS IoT (cushion/sync)       │
└──────────────────────────────────────┬───────────────────────────────┘
                                       │ MQTT / TLS  (cloud)
┌──────────────────────────────────────▼───────────────────────────────┐
│                     CLOUD (AWS IoT Core)                             │
│  IoT Core → Lambda → DynamoDB → API Gateway (REST)                  │
└──────────────────────────────────────────────────────────────────────┘
```

**Processing pipeline per sensor reading:**

```
MQTT message → JSON parse (Pydantic) → Temperature check
    → (person present?) → Normalise FSR features
    → AI inference (PyTorch or rule-based stub)
    → Session tracking → Alert decision
    → Vibration command (MQTT) + WebSocket broadcast
```

---

## Project Structure

```
smart-cushion-fog/
├── app.py                      # Main entry point (asyncio)
├── config/
│   └── settings.py             # All config via .env / pydantic-settings
├── core/
│   ├── mqtt_client.py          # paho-mqtt wrapper (thread-safe)
│   ├── websocket_server.py     # websockets broadcast server
│   ├── cloud_sync.py           # AWS IoT Core publisher (MQTT+TLS)
│   └── session_manager.py      # Rolling-window session & alert logic
├── ai/
│   ├── preprocessor.py         # Person detection + feature normalisation
│   ├── inference_engine.py     # PostureMLP (PyTorch) + rule-based stub
│   └── models/                 # ← Place your .pt file here (gitignored)
│       └── README.md
├── data/
│   └── schema.py               # Pydantic v2 message schemas
├── utils/
│   └── logger.py               # Colourised rotating log setup
├── tools/
│   ├── simulate_esp32.py       # CLI ESP32 simulator (publishes MQTT)
│   └── ws_monitor.html         # Browser WebSocket demo client
├── tests/                      # pytest unit tests
├── mosquitto/config/
│   └── mosquitto.conf          # Mosquitto config (auth required)
├── scripts/
│   └── generate_mqtt_credentials.sh   # Creates mosquitto/config/passwd
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example                # ← Copy to .env and fill in secrets
└── .gitignore                  # Excludes .env, certs/, *.pt, passwd
```

---

## Data Formats

### `cushion/raw` – ESP32 → Fog (MQTT)

```json
{
  "device_id":  "esp32-cushion-01",
  "timestamp":  1712345678.123,
  "sensors": {
    "fsr_top_left":      512,
    "fsr_top_right":     498,
    "fsr_bottom_left":   601,
    "fsr_bottom_right":  587,
    "temperature":       36.4
  }
}
```

> FSR values are 12-bit ADC readings (0–4095). Temperature is in °C.

### `cushion/control` – Fog → ESP32 (MQTT)

```json
{ "command": "vibrate", "duration_ms": 1000, "reason": "lean_left" }
```

### WebSocket broadcast – Fog → Web App

```json
{
  "timestamp":       1712345678.123,
  "posture":         "lean_left",
  "confidence":      0.87,
  "person_detected": true,
  "sensors":         { "...": "..." },
  "alert_sent":      true
}
```

### `cushion/sync` – Fog → AWS IoT Core (MQTT, every 60 s)

```json
{
  "device_id":         "esp32-cushion-01",
  "window_start":      1712345600.0,
  "window_end":        1712345660.0,
  "correct_seconds":   45.0,
  "incorrect_seconds": 15.0,
  "posture_counts":    { "correct": 9, "lean_left": 2, "lean_right": 1, "slouch_forward": 1, "lean_back": 0 }
}
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | Uses `asyncio.TaskGroup` |
| Docker + Docker Compose | Latest | For Mosquitto |
| Git | Any | Already in PATH |

---

## Quick Start (Docker)

### Step 1 – Clone and configure

```bash
git clone <your-repo-url>
cd smart-cushion-fog
cp .env.example .env
```

Edit `.env` and **change all `CHANGE_ME` values**:

```bash
# Choose strong values:
MQTT_PASSWORD=<strong_password>
WS_AUTH_TOKEN=<run: python -c "import secrets; print(secrets.token_hex(32))">
```

### Step 2 – Generate Mosquitto credentials

```bash
chmod +x scripts/generate_mqtt_credentials.sh
./scripts/generate_mqtt_credentials.sh
```

This creates `mosquitto/config/passwd` (gitignored) using bcrypt hashing.

### Step 3 – Start everything

```bash
docker compose up --build
```

You should see:
```
smart-cushion-fog  | ========================================================
smart-cushion-fog  |   Smart Cushion Fog Node
smart-cushion-fog  |   MQTT Broker  : mosquitto:1883
smart-cushion-fog  |   WebSocket    : ws://0.0.0.0:8765
smart-cushion-fog  |   AI Model     : ai/models/posture_model.pt
smart-cushion-fog  |   Cloud Sync   : disabled
smart-cushion-fog  | ========================================================
```

### Step 4 – Open the demo client

Open `tools/ws_monitor.html` in your browser and connect to `ws://localhost:8765`.

### Step 5 – Simulate sensor data

To avoid polluting your global Python environment, it is highly recommended to run the simulator either inside a virtual environment or directly via Docker.

**Option A: Virtual Environment (Recommended for testing)**
```bash
python -m venv venv
source venv/bin/activate
pip install paho-mqtt python-dotenv
python tools/simulate_esp32.py --scenario mixed --interval 0.5
```

**Option B: Via Docker (No local installation needed)**
```bash
# Run the simulator using the docker compose service
docker compose run --rm fog-node python tools/simulate_esp32.py --scenario mixed --interval 0.5
```

---

## Native Setup (no Docker)

If you cannot run Docker (e.g., on Windows without WSL2), you can run the application directly using your local Python installation.

### 🟡 Windows Quick Start (Automated)
We have provided a batch script that automatically sets up your environment, creates a default `.env` file, and installs dependencies.
1. Download or clone the code to your Windows machine.
2. Double click `start_windows.bat`
3. The script will automatically start the Launcher. (Note: you should open the `.env` file generated by the script to add your keys later).

### 🟢 Manual Setup (macOS / Linux / Windows)

```bash
# 1. Create virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt
pip install -r launcher/requirements.txt

# 3. Install and configure Mosquitto locally (Optional if using public broker)
brew install mosquitto       # macOS
# sudo apt install mosquitto  # Ubuntu

# 4. Copy mosquitto.conf to Mosquitto's config dir and set up passwd
mosquitto_passwd -c mosquitto/config/passwd fognode

# 5. Set environment
cp .env.example .env
# Edit .env: set MQTT_HOST=localhost (or broker IP), fill in credentials

# 6. Run the app/launcher
python run_launcher.py
```

---

## Configuration & Security

All configuration lives in `.env` – **never commit this file**.

### Security checklist

| Item | How it's handled |
|---|---|
| `.env` file | Listed in `.gitignore` – use `.env.example` as template |
| MQTT password | bcrypt-hashed in `mosquitto/config/passwd` (also gitignored) |
| Anonymous MQTT | Disabled in `mosquitto.conf` (`allow_anonymous false`) |
| WebSocket auth | Bearer token in `Authorization` header (set `WS_AUTH_TOKEN`) |
| AWS certs | Stored in `certs/` directory (gitignored) |
| Model weights | `ai/models/*.pt` is gitignored |
| Docker user | Runs as non-root `foguser` (UID 1001) |

### Generating a secure WebSocket token

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output into `WS_AUTH_TOKEN` in your `.env`.

### Key settings reference

| .env variable | Default | Description |
|---|---|---|
| `MQTT_HOST` | `mosquitto` | Broker hostname (use `localhost` for native) |
| `MQTT_USERNAME` | `fognode` | MQTT auth username |
| `MQTT_PASSWORD` | *required* | MQTT auth password |
| `WS_AUTH_TOKEN` | *empty* | WebSocket Bearer token (empty = no auth) |
| `TEMPERATURE_THRESHOLD` | `30.0` | °C below which seat is considered empty |
| `INCORRECT_POSTURE_ALERT_THRESHOLD` | `3` | Consecutive bad readings before vibration |
| `VIBRATION_DURATION_MS` | `1000` | Vibration motor on-duration |
| `CLOUD_ENABLED` | `false` | Enable AWS IoT Core sync |
| `CLOUD_SYNC_INTERVAL` | `60` | Seconds between cloud sync payloads |

---

## Running Tests

```bash
# Install test dependencies (already in requirements.txt)
pip install -r requirements.txt

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=. --cov-report=term-missing
```

Expected output:
```
tests/test_schema.py           ✓ PASSED (12 tests)
tests/test_preprocessor.py     ✓ PASSED (9 tests)
tests/test_inference.py        ✓ PASSED (10 tests)
tests/test_session_manager.py  ✓ PASSED (9 tests)
```

---

## Testing with the ESP32 Simulator

The simulator (`tools/simulate_esp32.py`) publishes realistic sensor JSON to
the MQTT broker, making it possible to test the full Fog Node pipeline without
physical hardware.

```bash
# Run in mixed mode (cycles through all postures)
python tools/simulate_esp32.py --scenario mixed --interval 0.5

# Simulate a specific posture
python tools/simulate_esp32.py --scenario lean_left --interval 1.0

# Publish exactly 20 readings and stop
python tools/simulate_esp32.py --scenario correct --count 20
```

Available scenarios: `correct`, `lean_left`, `lean_right`, `slouch_forward`,
`lean_back`, `empty`, `mixed`

---

## WebSocket Demo Client

Open `tools/ws_monitor.html` in any modern browser **on the same network** as
the Fog Node.

- Enter the WebSocket URL: `ws://<fog-node-ip>:8765`
- Enter your `WS_AUTH_TOKEN` value (or leave blank if token auth is disabled)
- Click **Connect**

The client displays:
- 🪑 **Cushion top-view** with FSR pressure dots that scale with force
- **Current posture** label + confidence bar
- Live **sensor readings** with animated bars
- **Event log** for alerts and connection events
- **Stats**: messages/sec, total alerts, correct readings

> 💡 The HTML client uses the standard native WebSocket API; no npm or build
> step required. Just open the file directly in the browser.

---

## Adding Your Trained PyTorch Model

1. Train a `PostureMLP` model (see `ai/inference_engine.py` for the architecture)
2. Save the state dict:
   ```python
   torch.save(model.state_dict(), "ai/models/posture_model.pt")
   ```
3. Verify the model loads:
   ```python
   from ai.inference_engine import InferenceEngine
   engine = InferenceEngine("ai/models/posture_model.pt")
   print("Using stub:", engine.using_stub)  # Should be False
   ```

**Model spec:**

| | Details |
|---|---|
| Input | `(batch, 4)` — normalised FSR values `[TL, TR, BL, BR]` in `[0, 1]` |
| Output | `(batch, 5)` — class logits (no softmax) |
| Class order | `0=correct, 1=lean_left, 2=lean_right, 3=slouch_forward, 4=lean_back` |
| Loss fn | `nn.CrossEntropyLoss` |

If no `.pt` file is present, the system automatically falls back to the
built-in rule-based classifier — the rest of the system is unaffected.

---

## Cloud Sync (AWS IoT Core)

Cloud sync is **disabled by default** (`CLOUD_ENABLED=false`).

### To enable:

1. Create a device in **AWS IoT Core Console** and download:
   - `certificate.pem.crt`
   - `private.pem.key`
   - `AmazonRootCA1.pem`

2. Place them in the `certs/` directory (already gitignored):
   ```
   certs/
   ├── certificate.pem.crt
   ├── private.pem.key
   └── AmazonRootCA1.pem
   ```

3. Update `.env`:
   ```env
   CLOUD_ENABLED=true
   AWS_ENDPOINT=xxxxxxxxxxxx-ats.iot.ap-northeast-1.amazonaws.com
   AWS_CLIENT_ID=fog-node-01
   ```

4. Attach an IoT policy to the certificate that allows:
   ```json
   { "Action": "iot:Publish", "Resource": "arn:aws:iot:*:*:topic/cushion/sync" }
   ```

---

## Architecture Advice

### Why Edge–Fog–Cloud?

| Tier | Role | Benefit |
|---|---|---|
| **Edge** (ESP32) | Sensor collection + actuator | Real-time hardware I/O, zero latency |
| **Fog** (this node) | AI inference + local serve | Fast response, complete privacy |
| **Cloud** (AWS) | Long-term storage + remote API | Historical analytics from anywhere |

### Key design decisions

1. **Privacy-first**: Raw sensor readings and posture classifications **never leave the local network**. Only anonymised summaries (seconds per posture per minute) are synced to the cloud.

2. **Offline-first**: The vibration alert and WebSocket stream operate **independently of cloud connectivity**. Users get feedback even if the internet is down.

3. **Graceful degradation**: If a trained PyTorch model is not yet available, the system falls back to heuristic classification automatically — no code change needed.

4. **Asyncio architecture**: All I/O is non-blocking. The paho-MQTT callback thread is bridged to the asyncio loop via a thread-safe queue, keeping everything single-threaded on the app side.

5. **Secrets separation**: All credentials are in `.env` (gitignored). Mosquitto uses bcrypt-hashed passwords. WebSocket uses a bearer token. AWS uses mutual TLS — no passwords stored in code.

### Future improvements

- **Calibration endpoint**: HTTP endpoint to capture individual sensor baseline offsets.
- **Model hot-reload**: Watch `ai/models/` for file changes and reload without restart.
- **Metrics endpoint**: Prometheus `/metrics` for Grafana dashboard.
- **MQTT TLS**: Enable `MQTT_USE_TLS=true` for encrypted local MQTT (between ESP32 and broker).
