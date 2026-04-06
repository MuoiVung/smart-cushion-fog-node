#!/usr/bin/env bash
# =============================================================
# scripts/generate_mqtt_credentials.sh
#
# Generates the Mosquitto password file for the Smart Cushion
# Fog Node. Run this once during initial setup.
#
# Usage:
#   chmod +x scripts/generate_mqtt_credentials.sh
#   ./scripts/generate_mqtt_credentials.sh
#
# What it does:
#   1. Reads MQTT_USERNAME and MQTT_PASSWORD from your .env file.
#   2. Creates mosquitto/config/passwd using mosquitto_passwd.
#   3. The passwd file is gitignored (contains bcrypt hashes only,
#      but we keep it out of the repo for safety).
#
# Requirements:
#   - Docker must be running (uses the mosquitto image to run
#     mosquitto_passwd without requiring a local install).
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$ROOT_DIR/.env"
PASSWD_FILE="$ROOT_DIR/mosquitto/config/passwd"

# ── Check .env exists ──────────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌  ERROR: .env file not found at $ENV_FILE"
  echo "   Copy .env.example to .env and fill in your values first."
  exit 1
fi

# ── Load credentials from .env ─────────────────────────────────────────────
MQTT_USERNAME=$(grep -E '^MQTT_USERNAME=' "$ENV_FILE" | cut -d '=' -f2- | tr -d '"'\' | xargs)
MQTT_PASSWORD=$(grep -E '^MQTT_PASSWORD=' "$ENV_FILE" | cut -d '=' -f2- | tr -d '"'\' | xargs)

if [[ -z "$MQTT_USERNAME" || -z "$MQTT_PASSWORD" ]]; then
  echo "❌  ERROR: MQTT_USERNAME or MQTT_PASSWORD not set in $ENV_FILE"
  exit 1
fi

if [[ "$MQTT_PASSWORD" == "CHANGE_ME"* ]]; then
  echo "❌  ERROR: Please change MQTT_PASSWORD from the placeholder value."
  exit 1
fi

# ── Create mosquitto/config dir ────────────────────────────────────────────
mkdir -p "$(dirname "$PASSWD_FILE")"

# ── Generate password file using Docker ───────────────────────────────────
echo "🔑  Generating Mosquitto password file for user: $MQTT_USERNAME"

# Use the official mosquitto image so we don't need a local install
docker run --rm \
  -v "$(dirname "$PASSWD_FILE"):/tmp/mosquitto_config" \
  eclipse-mosquitto:2 \
  sh -c "mosquitto_passwd -c -b /tmp/mosquitto_config/passwd '$MQTT_USERNAME' '$MQTT_PASSWORD'"

echo "✅  Password file created at: $PASSWD_FILE"
echo ""
echo "You can now run:  docker compose up"
