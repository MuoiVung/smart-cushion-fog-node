"""
ESP32 Simulator – Testing Tool for the Smart Cushion Fog Node.

Publishes synthetic sensor data to the Mosquitto MQTT broker,
mimicking the behaviour of the real ESP32 firmware. Use this tool
to test the Fog Node without physical hardware.

Usage:
    python tools/simulate_esp32.py [--scenario SCENARIO] [--interval 0.5]

Scenarios:
    correct          – Balanced pressure, correct posture (default)
    lean_left        – More pressure on left sensors
    lean_right       – More pressure on right sensors
    slouch_forward   – More pressure on front sensors
    lean_back        – More pressure on rear sensors
    mixed            – Cycles through all postures automatically
    empty            – Low temperature, no person detected

Requirements:
    pip install paho-mqtt python-dotenv
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

# Add project root to path so we can import settings
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import settings

# ---------------------------------------------------------------------------
# Sensor value presets (FSR range: 0–4095, temp in °C)
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, dict] = {
    "correct": {
        "description": "Balanced posture – equal pressure on all sensors",
        "sensors": {"tl": (1800, 2200), "tr": (1800, 2200), "bl": (1800, 2200), "br": (1800, 2200), "temp": (36.0, 37.2)},
    },
    "lean_left": {
        "description": "Leaning left – more pressure on left sensors",
        "sensors": {"tl": (2700, 3200), "tr": (400, 800),  "bl": (2700, 3200), "br": (400, 800),  "temp": (36.0, 37.2)},
    },
    "lean_right": {
        "description": "Leaning right – more pressure on right sensors",
        "sensors": {"tl": (400, 800),  "tr": (2700, 3200), "bl": (400, 800),  "br": (2700, 3200), "temp": (36.0, 37.2)},
    },
    "slouch_forward": {
        "description": "Slouching forward – more pressure on front sensors",
        "sensors": {"tl": (2700, 3200), "tr": (2700, 3200), "bl": (400, 800),  "br": (400, 800),  "temp": (36.0, 37.2)},
    },
    "lean_back": {
        "description": "Leaning back – more pressure on rear sensors",
        "sensors": {"tl": (400, 800),  "tr": (400, 800),   "bl": (2700, 3200), "br": (2700, 3200), "temp": (36.0, 37.2)},
    },
    "empty": {
        "description": "Empty cushion – no person detected",
        "sensors": {"tl": (0, 50),   "tr": (0, 50),   "bl": (0, 50),   "br": (0, 50),   "temp": (22.0, 25.0)},
    },
}

MIXED_CYCLE = ["correct", "correct", "correct", "lean_left", "lean_right",
               "slouch_forward", "lean_back", "empty"]


def generate_reading(scenario_name: str, device_id: str) -> dict:
    """Build a sensor JSON payload for the given scenario."""
    preset = SCENARIOS[scenario_name]["sensors"]

    def rval(key: str) -> int | float:
        lo, hi = preset[key]
        # Add small Gaussian noise to simulate real sensor jitter
        val = random.gauss((lo + hi) / 2, (hi - lo) / 6)
        val = max(lo, min(hi, val))
        return round(val, 2) if key == "temp" else int(val)

    return {
        "device_id": device_id,
        "timestamp": time.time(),
        "sensors": {
            "fsr_top_left":     rval("tl"),
            "fsr_top_right":    rval("tr"),
            "fsr_bottom_left":  rval("bl"),
            "fsr_bottom_right": rval("br"),
            "temperature":      rval("temp"),
        },
    }


def run_simulator(scenario: str, interval: float, count: int) -> None:
    """Connect to the broker and publish sensor readings in a loop."""

    client = mqtt.Client(
        client_id=f"esp32-simulator-{int(time.time())}",
        protocol=mqtt.MQTTv311,
    )

    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

    def on_connect(c, userdata, flags, rc):
        if rc == 0:
            print(f"[SIM] Connected to MQTT broker at {settings.mqtt_host}:{settings.mqtt_port}")
        else:
            print(f"[SIM] Connection failed (rc={rc})")
            sys.exit(1)

    client.on_connect = on_connect
    client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=30)
    client.loop_start()
    time.sleep(0.5)  # Wait for connection

    print(f"[SIM] Publishing to topic: {settings.mqtt_topic_raw}")
    print(f"[SIM] Scenario: {scenario}  |  Interval: {interval}s  |  Count: {'∞' if count == 0 else count}")
    if scenario == "mixed":
        print(f"[SIM] Mixed cycle: {MIXED_CYCLE}")
    print("[SIM] Press Ctrl+C to stop\n")

    published = 0
    mixed_idx = 0

    try:
        while True:
            if scenario == "mixed":
                active_scenario = MIXED_CYCLE[mixed_idx % len(MIXED_CYCLE)]
                mixed_idx += 1
            else:
                active_scenario = scenario

            payload = generate_reading(active_scenario, settings.device_id)
            payload_json = json.dumps(payload)

            result = client.publish(settings.mqtt_topic_raw, payload_json, qos=0)
            published += 1

            sensors = payload["sensors"]
            print(
                f"[SIM] #{published:04d}  scenario={active_scenario:<16}  "
                f"TL={sensors['fsr_top_left']:4d}  TR={sensors['fsr_top_right']:4d}  "
                f"BL={sensors['fsr_bottom_left']:4d}  BR={sensors['fsr_bottom_right']:4d}  "
                f"Temp={sensors['temperature']:5.1f}°C"
            )

            if count > 0 and published >= count:
                break

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n[SIM] Stopped after {published} messages")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Simulate ESP32 sensor readings for Smart Cushion Fog Node testing"
    )
    parser.add_argument(
        "--scenario", "-s",
        choices=list(SCENARIOS.keys()) + ["mixed"],
        default="mixed",
        help="Sensor scenario to simulate (default: mixed)",
    )
    parser.add_argument(
        "--interval", "-i",
        type=float,
        default=0.5,
        help="Publishing interval in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--count", "-c",
        type=int,
        default=0,
        help="Number of messages to publish (0 = infinite, default: 0)",
    )

    # Print available scenarios
    print("\nAvailable scenarios:")
    for name, cfg in SCENARIOS.items():
        print(f"  {name:<18} – {cfg['description']}")
    print(f"  {'mixed':<18} – Cycles through: {MIXED_CYCLE}\n")

    args = parser.parse_args()
    run_simulator(args.scenario, args.interval, args.count)
