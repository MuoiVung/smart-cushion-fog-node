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
import utils.paths as paths

# Initialize persistent directories and default templates
paths.ensure_directories()


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables / .env file.

    Field names are automatically mapped to UPPERCASE env var names:
        mqtt_host  ->  MQTT_HOST
        ws_port    ->  WS_PORT
        ... etc.
    """

    model_config = SettingsConfigDict(
        env_file=str(paths.get_env_path()),
        env_file_encoding="utf-8",
        extra="ignore",   # Silently ignore unknown env vars
    )

    # ── Device ─────────────────────────────────────────────────────────────
    device_id: str = Field(
        default="cushion-01",
        description="Unique identifier for this cushion device",
    )

    # ── Local MQTT Broker (Edge <-> Fog) ────────────────────────────────────
    mqtt_host:     str  = Field(default="localhost", description="Mosquitto broker hostname/IP")
    mqtt_port:     int  = Field(default=1883,        description="Mosquitto broker port")
    mqtt_username: str  = Field(default="",          description="MQTT username (from .env)")
    mqtt_password: str  = Field(default="",          description="MQTT password (from .env)")
    mqtt_use_tls:  bool = Field(default=False,       description="Enable TLS for local MQTT")
    mqtt_ca_cert:  str  = Field(default="",          description="Path to CA cert for MQTT TLS")

    mqtt_topic_raw:     str = Field(default="cushion/raw",     description="ESP32 → Fog (Interface 01)")
    mqtt_topic_command: str = Field(default="cushion/command", description="Fog → ESP32 (Interface 05)")

    @property
    def mqtt_topic_control(self) -> str:
        """Legacy alias for mqtt_topic_command."""
        return self.mqtt_topic_command

    # ── WebSocket Server (Fog -> Web App) ───────────────────────────────────
    ws_host:       str = Field(default="0.0.0.0", description="WebSocket listen address")
    ws_port:       int = Field(default=8765,      description="WebSocket listen port")
    ws_auth_token: str = Field(
        default="",
        description="Bearer token clients must send. Empty string disables auth (dev only).",
    )

    # ── AI Model (system_architecture.md §1) ─────────────────────────────────
    model_path:  str = Field(
        default="ai/models/smart_cushion_model.h5",
        description="Path to the Keras CNN model (.h5) trained in smart-cushion-AI",
    )
    scaler_path: str = Field(
        default="ai/models/fsr_scaler.pkl",
        description="Path to the sklearn MinMaxScaler (.pkl) used during CNN training",
    )
    temperature_threshold: float = Field(
        default=30.0,
        description=(
            "DEPRECATED — temperature is NOT used for occupancy detection. "
            "AI model handles occupancy via FSR pressure. Retained for compatibility."
        ),
    )

    # ── Alert / Vibration Settings ────────────────────────────────────────────
    vibration_duration_ms:             int = Field(default=1000, ge=100, le=10000)
    vibration_intensity:               int = Field(
        default=200, ge=0, le=255,
        description="PWM duty cycle (0–255) for vibration motor intensity",
    )
    vibration_enabled:                 bool = Field(
        default=False,
        description="Globally enable/disable vibration alerts",
    )
    incorrect_posture_alert_threshold: int = Field(
        default=3,
        description="Trigger vibration after this many consecutive incorrect posture readings",
    )

    # ── Cloud Sync (AWS IoT Core) ────────────────────────────────────────────
    cloud_enabled:       bool = Field(default=False, description="Enable/disable cloud sync")
    aws_endpoint:        str  = Field(default="",    description="AWS IoT Core endpoint URL")
    aws_client_id:       str  = Field(default="fog-node-01")
    aws_cert_path:       str  = Field(default="certs/certificate.pem.crt")
    aws_key_path:        str  = Field(default="certs/private.pem.key")
    aws_ca_path:         str  = Field(default="certs/AmazonRootCA1.pem")
    aws_topic_event:     str  = Field(default="cushion/{device_id}/event",     description="Interface 03 event topic template")
    aws_topic_telemetry: str  = Field(default="cushion/{device_id}/telemetry", description="Interface 03 telemetry topic template")
    aws_topic_summary:   str  = Field(default="cushion/{device_id}/summary",   description="Interface 03 summary topic template")
    # Legacy alias
    aws_topic_sync:      str  = Field(default="cushion/sync")
    cloud_sync_interval: int  = Field(default=60, ge=10, description="Telemetry sync interval in seconds")
    
    # -- Discovery Service (Firebase) -----------------------------------------
    discovery_firebase_url: str = Field(
        default="",
        description="Firebase Realtime Database URL for IP reporting"
    )

    # ── Cloud WebSocket Relay (AWS API Gateway WebSocket) ─────────────────────
    cloud_ws_url: str = Field(
        default="",
        description="AWS API Gateway WebSocket URL (wss://xxx.execute-api.region.amazonaws.com/prod). "
                    "When set, Fog relays realtime updates to App clients over the internet.",
    )

    # ── Computed helpers ─────────────────────────────────────────────────────
    @property
    def mqtt_broker_url(self) -> str:
        scheme = "mqtts" if self.mqtt_use_tls else "mqtt"
        return f"{scheme}://{self.mqtt_host}:{self.mqtt_port}"

    @property
    def model_path_resolved(self) -> str:
        return paths.resolve_model_path(self.model_path)

    @property
    def scaler_path_resolved(self) -> str:
        return paths.resolve_model_path(self.scaler_path)

    @property
    def aws_cert_path_resolved(self) -> str:
        return paths.resolve_model_path(self.aws_cert_path)

    @property
    def aws_key_path_resolved(self) -> str:
        return paths.resolve_model_path(self.aws_key_path)

    @property
    def aws_ca_path_resolved(self) -> str:
        return paths.resolve_model_path(self.aws_ca_path)


# Singleton instance – import this everywhere instead of creating new Settings()
settings = Settings()
