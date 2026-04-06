"""
Central configuration for the Smart Cushion Fog Node.

All configurable values are loaded from the .env file via pydantic-settings.
Sensitive fields (passwords, tokens, cert paths) must NEVER be hardcoded here.

Usage:
    from config.settings import settings
    print(settings.mqtt_host)
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables / .env file.

    Field names are automatically mapped to UPPERCASE env var names:
        mqtt_host  ->  MQTT_HOST
        ws_port    ->  WS_PORT
        ... etc.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",   # Silently ignore unknown env vars
    )

    # ── Device ─────────────────────────────────────────────────────────────
    device_id: str = Field(
        default="esp32-cushion-01",
        description="Unique identifier matching the ESP32 firmware config",
    )

    # ── Local MQTT Broker (Edge <-> Fog) ────────────────────────────────────
    mqtt_host:     str  = Field(default="localhost", description="Mosquitto broker hostname/IP")
    mqtt_port:     int  = Field(default=1883,        description="Mosquitto broker port")
    mqtt_username: str  = Field(default="",          description="MQTT username (from .env)")
    mqtt_password: str  = Field(default="",          description="MQTT password (from .env)")
    mqtt_use_tls:  bool = Field(default=False,       description="Enable TLS for local MQTT")
    mqtt_ca_cert:  str  = Field(default="",          description="Path to CA cert for MQTT TLS")

    mqtt_topic_raw:     str = Field(default="cushion/raw",     description="ESP32 -> Fog topic")
    mqtt_topic_control: str = Field(default="cushion/control", description="Fog -> ESP32 topic")

    # ── WebSocket Server (Fog -> Web App) ───────────────────────────────────
    ws_host:       str = Field(default="0.0.0.0", description="WebSocket listen address")
    ws_port:       int = Field(default=8765,      description="WebSocket listen port")
    ws_auth_token: str = Field(
        default="",
        description="Bearer token clients must send. Empty string disables auth (dev only).",
    )

    # ── AI Model ─────────────────────────────────────────────────────────────
    model_path:            str   = Field(default="ai/models/posture_model.pt",
                                         description="Path to the trained PyTorch model weights")
    temperature_threshold: float = Field(
        default=30.0,
        description="Temperature (°C) below this value indicates no person is present",
    )

    # ── Alert / Vibration Settings ────────────────────────────────────────────
    vibration_duration_ms:              int = Field(default=1000, ge=100, le=10000)
    incorrect_posture_alert_threshold:  int = Field(
        default=3,
        description="Trigger vibration after this many consecutive incorrect readings",
    )

    # ── Cloud Sync (AWS IoT Core) ────────────────────────────────────────────
    cloud_enabled:       bool = Field(default=False, description="Enable/disable cloud sync")
    aws_endpoint:        str  = Field(default="",    description="AWS IoT Core endpoint URL")
    aws_client_id:       str  = Field(default="fog-node-01")
    aws_cert_path:       str  = Field(default="certs/certificate.pem.crt")
    aws_key_path:        str  = Field(default="certs/private.pem.key")
    aws_ca_path:         str  = Field(default="certs/AmazonRootCA1.pem")
    aws_topic_sync:      str  = Field(default="cushion/sync")
    cloud_sync_interval: int  = Field(default=60, ge=10, description="Sync interval in seconds")

    # ── Computed helpers ─────────────────────────────────────────────────────
    @property
    def mqtt_broker_url(self) -> str:
        scheme = "mqtts" if self.mqtt_use_tls else "mqtt"
        return f"{scheme}://{self.mqtt_host}:{self.mqtt_port}"


# Singleton instance – import this everywhere instead of creating new Settings()
settings = Settings()
