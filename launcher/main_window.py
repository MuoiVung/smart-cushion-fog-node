"""
Fog Node Launcher – Main Window (CustomTkinter GUI)

Cross-platform desktop application that provides a friendly GUI for:
  - Starting / stopping Docker Compose services
  - Selecting the AI model (Keras, Random Forest, or XGBoost)
  - Monitoring all 4 data channels in real time
  - Viewing console output from Docker

Layout:
  ┌─────────────────────────────────────────────────────────────────┐
  │  Header: title + app status                                     │
  ├───────────────────────┬─────────────────────────────────────────┤
  │  Service Control      │  System Status                          │
  ├───────────────────────┴─────────────────────────────────────────┤
  │  AI Model Configuration                                         │
  ├─────────────────────────────────────────────────────────────────┤
  │  Data Monitor (4 channel tabs)                                  │
  ├─────────────────────────────────────────────────────────────────┤
  │  Console Log                                                    │
  └─────────────────────────────────────────────────────────────────┘
"""

import os
import queue
import re
import threading
import subprocess
import socket
import sys
import requests
import json
import time
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional, Dict, Any

import customtkinter as ctk

from launcher.docker_manager import DockerManager, ServiceState, ServiceStatus
from launcher.mqtt_monitor import MQTTMonitor, MonitorMessage
from launcher.ws_monitor import WebSocketMonitor
from launcher.dashboard_panel import DashboardPanel
from launcher.data_collector_panel import DataCollectorPanel
from core.local_db import LocalDB
import utils.paths as paths

# ── Theme ─────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Palette ───────────────────────────────────────────────────────────────
COLOR = {
    "green":   "#3fb950",
    "red":     "#f85149",
    "yellow":  "#d29922",
    "blue":    "#58a6ff",
    "purple":  "#bc8cff",
    "muted":   "#7d8590",
    "surface": "#161b22",
    "bg":      "#0d1117",
    "text":    "#e6edf3",
}

# Channel metadata for the monitor tabs
CHANNELS = [
    {"key": "esp32_to_fog",  "label": "📡  ESP32 → Fog",   "color": COLOR["blue"]},
    {"key": "fog_to_esp32",  "label": "📤  Fog → ESP32",   "color": COLOR["red"]},
    {"key": "fog_to_cloud",  "label": "☁️   Fog → Cloud",  "color": COLOR["purple"]},
    {"key": "fog_to_app",    "label": "📱  Fog → App",     "color": COLOR["green"]},
    {"key": "ai_results",    "label": "🧠  AI Predictions", "color": COLOR["yellow"]},
]

PROJECT_ROOT = paths.PROJECT_ROOT
DATA_ROOT = paths.DATA_ROOT


def _read_env(key: str, default: str = "") -> str:
    """Read a single key from the project .env file."""
    env_path = paths.get_env_path()
    if not env_path.exists():
        return default
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def _write_env(key: str, value: str) -> None:
    """Update or add a key in the project .env file."""
    env_path = paths.get_env_path()
    if not env_path.exists():
        return
    lines  = env_path.read_text(encoding="utf-8").splitlines()
    new    = []
    found  = False
    for line in lines:
        if line.startswith(f"{key}="):
            new.append(f"{key}={value}")
            found = True
        else:
            new.append(line)
    if not found:
        new.append(f"{key}={value}")
    env_path.write_text("\n".join(new) + "\n", encoding="utf-8")


# =============================================================================
# Main application window
# =============================================================================

class FogLauncherApp(ctk.CTk):
    """Top-level application window."""

    POLL_MS    = 250   # UI queue poll interval (ms)
    MAX_LOG    = 500   # Max lines per text widget

    def __init__(self) -> None:
        super().__init__()

        self.title("🪑  Smart Cushion – Fog Node Launcher")
        self.geometry("1000x820")
        self.minsize(860, 700)
        self.configure(fg_color=COLOR["bg"])

        # Thread-safe queues
        self._log_queue: queue.Queue[str]            = queue.Queue()
        self._msg_queue: queue.Queue[MonitorMessage] = queue.Queue()

        # Managers
        self._docker = DockerManager(
            project_root=PROJECT_ROOT,
            on_status=self._on_docker_status,
            on_log=self._log_queue.put,
        )
        self._mqtt_monitor = MQTTMonitor(
            host="localhost",
            port=int(_read_env("MQTT_PORT", "1883")),
            username=_read_env("MQTT_USERNAME", ""),
            password=_read_env("MQTT_PASSWORD", ""),
            on_message=self._msg_queue.put,
            on_log=self._log_queue.put,
        )
        self._ws_monitor = WebSocketMonitor(
            host="localhost",
            port=int(_read_env("WS_PORT", "8765")),
            token=_read_env("WS_AUTH_TOKEN", ""),
            on_message=self._msg_queue.put,
            on_log=self._log_queue.put,
        )

        # Discovery configuration (Securely loaded from .env)
        self.firebase_url = _read_env("DISCOVERY_FIREBASE_URL", "")
        self.device_id    = _read_env("DEVICE_ID", "cushion-01")

        self._current_status: Optional[ServiceStatus] = None
        self._discovery_timer: Optional[str]          = None
        self._monitor_paused = False
        self._current_status = ServiceStatus()
        self._monitors_started = False
        self._mqtt_connected = False
        self._ws_connected = False
        self._success_logged = False   # Guard: start monitors only once per Start

        # Local DB (Config Store + Offline Cloud Queue)
        self._db = LocalDB()

        self._build_ui()
        self._start_poll()
        
        # Report Host IP to Firebase for discovery
        self.after(2000, self.report_discovery_ip)

        # Shutdown cleanly when the window is closed
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # =========================================================================
    # UI Builder
    # =========================================================================

    def _build_ui(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # ── Sidebar ───────────────────────────────────────────────────────────
        self._build_sidebar()

        # ── Main Content Area ─────────────────────────────────────────────────
        self.main_content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_content_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        self.main_content_frame.grid_rowconfigure(0, weight=1)
        self.main_content_frame.grid_columnconfigure(0, weight=1)

        # ── Views ─────────────────────────────────────────────────────────────
        self._build_dashboard_view()
        self._build_config_view()
        self._build_monitor_view()
        self._build_data_collector_view()

        # Select default
        self._select_nav("dashboard")

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=COLOR["surface"])
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(6, weight=1) # Spacer

        # Logo/Title
        title = ctk.CTkLabel(sidebar, text="🪑 Smart Cushion", font=ctk.CTkFont(size=20, weight="bold"))
        title.grid(row=0, column=0, padx=20, pady=(20, 10))

        # Header Status
        self._header_status = ctk.CTkLabel(
            sidebar, text="● Services stopped",
            font=ctk.CTkFont(size=12), text_color=COLOR["muted"]
        )
        self._header_status.grid(row=1, column=0, padx=20, pady=(0, 20))

        # Nav Buttons
        self._nav_buttons = {}
        
        def make_nav(name, text, row):
            btn = ctk.CTkButton(
                sidebar, text=text, fg_color="transparent",
                text_color=COLOR["text"], anchor="w",
                font=ctk.CTkFont(size=14, weight="bold"),
                command=lambda n=name: self._select_nav(n)
            )
            btn.grid(row=row, column=0, padx=20, pady=5, sticky="ew")
            self._nav_buttons[name] = btn

        make_nav("dashboard", "📊 Live Dashboard", 2)
        make_nav("collector", "🎯 Data Collection", 3)
        make_nav("config", "⚙️ Config & Control", 4)
        make_nav("monitor", "📋 Logs & Raw Data", 5)
        
        # Start/Stop buttons in sidebar bottom
        self._start_btn = ctk.CTkButton(
            sidebar, text="▶  Start Services", font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=COLOR["green"], hover_color="#2ea043", text_color="#ffffff",
            command=self._on_start
        )
        self._start_btn.grid(row=7, column=0, padx=20, pady=10, sticky="ew")

        self._stop_btn = ctk.CTkButton(
            sidebar, text="■  Stop Services", font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#cf222e", hover_color="#a40e26", text_color="#ffffff",
            state="disabled", command=self._on_stop
        )
        self._stop_btn.grid(row=8, column=0, padx=20, pady=10, sticky="ew")

        self._rebuild_btn = ctk.CTkButton(
            sidebar, text="🔨  Rebuild Services", font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=COLOR["muted"], hover_color="#484f58", text_color="#ffffff",
            command=self._on_rebuild
        )
        self._rebuild_btn.grid(row=9, column=0, padx=20, pady=(0, 20), sticky="ew")

    def _build_dashboard_view(self) -> None:
        self._view_dashboard = ctk.CTkFrame(self.main_content_frame, fg_color="transparent")
        self._view_dashboard.grid(row=0, column=0, sticky="nsew")
        self._view_dashboard.grid_rowconfigure(0, weight=1)
        self._view_dashboard.grid_columnconfigure(0, weight=1)
        
        self._dashboard_panel = DashboardPanel(self._view_dashboard)
        self._dashboard_panel.grid(row=0, column=0, sticky="nsew")
        self._dashboard = self._dashboard_panel

    def _build_config_view(self) -> None:
        self._view_config = ctk.CTkScrollableFrame(self.main_content_frame, fg_color="transparent")
        self._view_config.grid(row=0, column=0, sticky="nsew")
        self._view_config.grid_columnconfigure(0, weight=1)

        row1 = ctk.CTkFrame(self._view_config, fg_color="transparent")
        row1.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        row1.grid_columnconfigure(0, weight=1)
        self._build_status_panel(row1)

        self._build_model_panel(self._view_config)
        self._build_smoothing_panel(self._view_config)
        self._build_cloud_queue_panel(self._view_config)

    def _build_monitor_view(self) -> None:
        self._view_monitor = ctk.CTkFrame(self.main_content_frame, fg_color="transparent")
        self._view_monitor.grid(row=0, column=0, sticky="nsew")
        self._view_monitor.grid_rowconfigure(0, weight=2)
        self._view_monitor.grid_rowconfigure(1, weight=1)
        self._view_monitor.grid_columnconfigure(0, weight=1)
        
        self._build_monitor_panel(self._view_monitor)
        self._build_console_panel(self._view_monitor)

    def _build_data_collector_view(self) -> None:
        self._view_collector = ctk.CTkScrollableFrame(self.main_content_frame, fg_color="transparent")
        self._view_collector.grid(row=0, column=0, sticky="nsew")
        self._view_collector.grid_columnconfigure(0, weight=1)
        
        self._data_collector = DataCollectorPanel(
            self._view_collector, 
            retrain_callback=self._on_retrain_ai
        )
        self._data_collector.grid(row=0, column=0, sticky="nsew")

    def _select_nav(self, name: str) -> None:
        views = {
            "dashboard": self._view_dashboard,
            "collector": self._view_collector,
            "config": self._view_config,
            "monitor": self._view_monitor
        }
        
        if name == "collector" and hasattr(self, "_data_collector"):
            self._data_collector.refresh_train_dataset_options()
            
        for k, v in views.items():
            if k == name:
                v.grid()
                self._nav_buttons[k].configure(fg_color="#1a2537", border_color=COLOR["blue"], border_width=1)
            else:
                v.grid_remove()
                self._nav_buttons[k].configure(fg_color="transparent", border_width=0)



    def _build_status_panel(self, parent) -> None:
        frame = ctk.CTkFrame(parent, fg_color=COLOR["surface"], corner_radius=12)
        frame.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame, text="SYSTEM STATUS",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLOR["muted"],
        ).grid(row=0, column=0, columnspan=2, padx=16, pady=(14, 6), sticky="w")

        # Mosquitto row
        self._dot_mosquitto = ctk.CTkLabel(frame, text="●", text_color=COLOR["muted"], font=ctk.CTkFont(size=16))
        self._dot_mosquitto.grid(row=1, column=0, padx=(16, 6))
        ctk.CTkLabel(frame, text="Mosquitto MQTT Broker",
                     text_color=COLOR["text"], font=ctk.CTkFont(size=12)
                     ).grid(row=1, column=1, sticky="w", pady=4)
        self._lbl_mosquitto = ctk.CTkLabel(frame, text="Stopped", text_color=COLOR["muted"],
                                            font=ctk.CTkFont(size=11))
        self._lbl_mosquitto.grid(row=1, column=2, padx=16)

        # Fog-node row
        self._dot_fog = ctk.CTkLabel(frame, text="●", text_color=COLOR["muted"], font=ctk.CTkFont(size=16))
        self._dot_fog.grid(row=2, column=0, padx=(16, 6))
        ctk.CTkLabel(frame, text="Fog Node (AI Engine)",
                     text_color=COLOR["text"], font=ctk.CTkFont(size=12)
                     ).grid(row=2, column=1, sticky="w", pady=4)
        self._lbl_fog = ctk.CTkLabel(frame, text="Stopped", text_color=COLOR["muted"],
                                      font=ctk.CTkFont(size=11))
        self._lbl_fog.grid(row=2, column=2, padx=16)

        # Message counter
        ctk.CTkLabel(frame, text="Messages received:",
                     text_color=COLOR["muted"], font=ctk.CTkFont(size=11)
                     ).grid(row=3, column=0, columnspan=2, padx=16, pady=(6, 2), sticky="w")
        self._lbl_msg_count = ctk.CTkLabel(frame, text="0",
                                            text_color=COLOR["blue"],
                                            font=ctk.CTkFont(size=13, weight="bold"))
        self._lbl_msg_count.grid(row=3, column=2, padx=16)
        self._total_msgs = 0

        # Vibration Toggle (Always starts as OFF by default)
        self._vibration_enabled_var = ctk.BooleanVar(value=True)
        self._vibration_switch = ctk.CTkSwitch(
            frame, text="Vibration Alerts",
            variable=self._vibration_enabled_var,
            onvalue=True, offvalue=False,
            font=ctk.CTkFont(size=12, weight="bold"),
            progress_color=COLOR["green"],
            command=self._on_toggle_vibration
        )
        self._vibration_switch.grid(row=4, column=0, columnspan=3, padx=16, pady=(10, 14), sticky="w")

    def _on_toggle_vibration(self) -> None:
        val = self._vibration_enabled_var.get()
        
        # 1. Send instantaneous command via MQTT
        if self._mqtt_connected:
            self._mqtt_monitor.publish_config("vibration_enabled", val)
            self._log_console(f"Vibration {'enabled' if val else 'disabled'} (Instant update sent)")
        else:
            self._log_console(f"Vibration toggle changed, but Fog Node is not connected.")


    # ── AI Model Configuration ────────────────────────────────────────────────

    def _build_model_panel(self, parent) -> None:
        frame = ctk.CTkFrame(parent, fg_color=COLOR["surface"], corner_radius=12)
        frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(12, 0))
        frame.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            frame, text="AI MODEL CONFIGURATION",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLOR["muted"],
        ).grid(row=0, column=0, columnspan=4, padx=16, pady=(14, 8), sticky="w")

        # ── Mode selector ─────────────────────────────────────────────────
        initial_mode = self._db.get_config("model_type", "random_forest")
        if initial_mode not in ["keras", "random_forest", "fnn", "tiny_cnn", "resnet"]:
            initial_mode = "random_forest"
        self._model_mode = ctk.StringVar(value=initial_mode)

        # Row 1: Random Forest | Hybrid FNN
        ctk.CTkRadioButton(
            frame, text="Random Forest (.pkl)",
            variable=self._model_mode, value="random_forest",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_model_mode_change,
        ).grid(row=1, column=0, padx=16, pady=4, sticky="w")

        ctk.CTkRadioButton(
            frame, text="Hybrid FNN (.keras)",
            variable=self._model_mode, value="fnn",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_model_mode_change,
        ).grid(row=1, column=1, padx=8, pady=4, sticky="w")

        # Row 2: Tiny CNN | Micro ResNet | Keras Legacy
        ctk.CTkRadioButton(
            frame, text="Tiny CNN (.keras)",
            variable=self._model_mode, value="tiny_cnn",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_model_mode_change,
        ).grid(row=2, column=0, padx=16, pady=4, sticky="w")

        ctk.CTkRadioButton(
            frame, text="Micro ResNet (.keras)",
            variable=self._model_mode, value="resnet",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_model_mode_change,
        ).grid(row=2, column=1, padx=8, pady=4, sticky="w")

        ctk.CTkRadioButton(
            frame, text="CNN Legacy (.h5)",
            variable=self._model_mode, value="keras",
            font=ctk.CTkFont(size=12),
            command=self._on_model_mode_change,
        ).grid(row=2, column=2, padx=8, pady=4, sticky="w")

        # ── Row 3: Model file ───────────────────────────────────────────────
        self._model_lbl = ctk.CTkLabel(
            frame, text="Model File:",
            font=ctk.CTkFont(size=11), text_color=COLOR["muted"],
        )
        self._model_lbl.grid(row=3, column=0, padx=16, pady=4, sticky="e")

        self._model_path_var = ctk.StringVar(
            value=self._db.get_config("model_path", _read_env("MODEL_PATH", "ai/models/posture_9_model.h5"))
        )
        self._model_entry = ctk.CTkEntry(
            frame,
            textvariable=self._model_path_var,
            font=ctk.CTkFont(family="Courier", size=11),
            state="disabled",
            corner_radius=6,
            height=32,
        )
        self._model_entry.grid(row=3, column=1, columnspan=2, padx=8, pady=4, sticky="ew")

        self._browse_btn = ctk.CTkButton(
            frame, text="Browse…", width=80, height=32,
            fg_color=COLOR["surface"], border_color=COLOR["blue"],
            border_width=1, text_color=COLOR["blue"],
            hover_color="#1a2537", state="disabled",
            command=self._on_browse_model,
        )
        self._browse_btn.grid(row=3, column=3, padx=(0, 8), pady=4)

        # ── Row 4: Scaler file (.pkl) ───────────────────────────────────────
        self._scaler_lbl = ctk.CTkLabel(
            frame, text="Scaler (.pkl):",
            font=ctk.CTkFont(size=11), text_color=COLOR["muted"],
        )
        self._scaler_lbl.grid(row=4, column=0, padx=16, pady=4, sticky="e")

        self._scaler_path_var = ctk.StringVar(
            value=self._db.get_config("scaler_path", _read_env("SCALER_PATH", "ai/models/fsr_scaler_9.pkl"))
        )
        self._scaler_entry = ctk.CTkEntry(
            frame,
            textvariable=self._scaler_path_var,
            font=ctk.CTkFont(family="Courier", size=11),
            state="disabled",
            corner_radius=6,
            height=32,
        )
        self._scaler_entry.grid(row=4, column=1, columnspan=2, padx=8, pady=4, sticky="ew")

        self._scaler_browse_btn = ctk.CTkButton(
            frame, text="Browse…", width=80, height=32,
            fg_color=COLOR["surface"], border_color=COLOR["blue"],
            border_width=1, text_color=COLOR["blue"],
            hover_color="#1a2537", state="disabled",
            command=self._on_browse_scaler,
        )
        self._scaler_browse_btn.grid(row=4, column=3, padx=(0, 8), pady=4)

        # ── Row 5: Match status label ───────────────────────────────────────
        self._model_match_label = ctk.CTkLabel(
            frame, text="",
            font=ctk.CTkFont(size=11),
            text_color=COLOR["green"],
        )
        self._model_match_label.grid(row=5, column=0, columnspan=4, padx=16, pady=(0, 2), sticky="w")

        # ── Row 6: Apply / Hot-Reload button ───────────────────────────────
        self._apply_btn = ctk.CTkButton(
            frame, text="Apply & Restart Fog Node",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLOR["blue"],
            hover_color="#1f6feb",
            text_color="#0d1117",
            corner_radius=8,
            height=32,
            command=self._on_apply_model,
        )
        self._apply_btn.grid(row=6, column=0, columnspan=4, padx=16, pady=(4, 14), sticky="w")

        # Sync UI state with default mode
        self._on_model_mode_change()


    # ── Data Monitor ──────────────────────────────────────────────────────────

    # ── AI Prediction Quality (Smoothing + Confidence) ─────────────────────

    def _build_smoothing_panel(self, parent) -> None:
        """Panel for configuring AI confidence threshold and temporal smoothing."""
        frame = ctk.CTkFrame(parent, fg_color=COLOR["surface"], corner_radius=12)
        frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(12, 0))
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame, text="AI PREDICTION QUALITY",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLOR["muted"],
        ).grid(row=0, column=0, columnspan=4, padx=16, pady=(14, 6), sticky="w")

        # Helper to create a labelled row with tooltip-like description
        def _add_row(row_idx, label, desc, var, unit=""):
            ctk.CTkLabel(
                frame, text=label,
                font=ctk.CTkFont(size=12, weight="bold"), text_color=COLOR["text"],
            ).grid(row=row_idx, column=0, padx=16, pady=(6, 0), sticky="w")
            ctk.CTkLabel(
                frame, text=desc,
                font=ctk.CTkFont(size=10), text_color=COLOR["muted"],
                wraplength=380, justify="left",
            ).grid(row=row_idx + 1, column=0, padx=16, pady=(0, 4), sticky="w")
            entry = ctk.CTkEntry(
                frame, textvariable=var, width=80, height=30,
                font=ctk.CTkFont(size=12),
            )
            entry.grid(row=row_idx, column=1, padx=(8, 4), pady=(6, 0), sticky="w")
            if unit:
                ctk.CTkLabel(frame, text=unit, font=ctk.CTkFont(size=11),
                             text_color=COLOR["muted"]).grid(
                    row=row_idx, column=2, padx=(0, 16), sticky="w")
            return entry

        # Min Confidence
        self._smooth_conf_var = ctk.StringVar(
            value=self._db.get_config("min_confidence", "0.70")
        )
        _add_row(
            1,
            "Min Confidence",
            "Reject AI predictions below this threshold (0.0 – 1.0).\n"
            "Low-confidence frames are skipped; last accepted posture is held.",
            self._smooth_conf_var,
            unit="(0.0–1.0)",
        )

        # Smoothing Window Size
        self._smooth_window_var = ctk.StringVar(
            value=self._db.get_config("smoothing_window_size", "10")
        )
        _add_row(
            3,
            "Window Size",
            "Number of recent predictions kept in memory for voting.\n"
            "E.g. 10 = last 5 seconds at 0.5 s/frame. Larger = slower reaction.",
            self._smooth_window_var,
            unit="frames",
        )

        # Min Votes
        self._smooth_votes_var = ctk.StringVar(
            value=self._db.get_config("smoothing_min_votes", "7")
        )
        _add_row(
            5,
            "Min Votes to Confirm",
            "Out of the Window Size frames, how many must agree on the same\n"
            "posture before it is accepted. Aim for 70-80 % of Window Size.",
            self._smooth_votes_var,
            unit="votes",
        )

        # Status label
        self._smooth_status_lbl = ctk.CTkLabel(
            frame, text="",
            font=ctk.CTkFont(size=11), text_color=COLOR["green"],
        )
        self._smooth_status_lbl.grid(row=7, column=0, columnspan=4, padx=16, pady=(4, 2), sticky="w")

        # Apply button
        ctk.CTkButton(
            frame,
            text="Apply  (hot-update via MQTT)",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=COLOR["blue"], hover_color="#1f6feb",
            text_color="#0d1117", corner_radius=8, height=32,
            command=self._on_apply_smoothing,
        ).grid(row=8, column=0, columnspan=4, padx=16, pady=(4, 14), sticky="w")

    def _on_apply_smoothing(self) -> None:
        """Validate and hot-send smoothing config via MQTT; persist to LocalDB."""
        pass # implementation removed for brevity

    def _on_retrain_ai(self, model_type: str, dataset_path: str) -> None:
        """Triggers the specific AI training script in a separate thread."""
        def run_train():
            try:
                self._data_collector.btn_retrain.configure(text="⏳ TRAINING...", state="disabled")
                self._data_collector.clear_retrain_log()
                
                scripts = {
                    "random_forest": "train_rf.py",
                    "fnn":           "train_fnn.py",
                    "tiny_cnn":      "train_tiny_cnn.py",
                    "resnet":        "train_resnet.py",
                    "keras":         "train_cnn_v4_deprecated.py",   # legacy
                }
                script_name = scripts.get(model_type, "train_rf.py")
                
                start_msg = f"🧠 Starting AI Retraining ({model_type.upper()})... using {script_name}"
                ds_msg = f"📁 Dataset Path: {dataset_path}"
                self._log_console(start_msg)
                self._log_console(ds_msg)
                self._data_collector.write_retrain_log(start_msg)
                self._data_collector.write_retrain_log(ds_msg)
                
                # Command to run training
                ai_dir = PROJECT_ROOT / "ai"
                script_path = ai_dir / script_name
                
                if not script_path.exists():
                    err = f"❌ Error: Script not found at {script_path}"
                    self._log_console(err)
                    self._data_collector.write_retrain_log(err)
                    return

                # Use the same python interpreter running the launcher
                is_frozen = getattr(sys, 'frozen', False)
                if is_frozen:
                    cmd = [sys.executable, "--train", model_type, dataset_path]
                else:
                    cmd = [sys.executable, str(script_path), dataset_path]
                
                exec_msg = f"🛠️ Executing: {' '.join(cmd)} in {PROJECT_ROOT}"
                self._log_console(exec_msg)
                self._data_collector.write_retrain_log(exec_msg)
                self._data_collector.write_retrain_log("-" * 60)
                
                process = subprocess.Popen(
                    cmd, cwd=str(PROJECT_ROOT), 
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                )
                
                # Log status lines in real-time
                for line in iter(process.stdout.readline, ""):
                    line_clean = line.strip()
                    if line_clean:
                        # Log to system console selectively to avoid spamming
                        if any(x in line_clean for x in ["Epoch", "Fold", "Accuracy", "✅", "🚀", "Error", "Exception"]):
                            self._log_console(f"  [AI] {line_clean}")
                        elif len(line_clean) < 100:
                            self._log_console(f"  [AI] {line_clean}")
                        
                        # Ghi toàn bộ logs thô vào TextBox chuyên dụng trên giao diện
                        self._data_collector.write_retrain_log(line_clean)
                
                process.wait()
                
                self._data_collector.write_retrain_log("-" * 60)
                if process.returncode == 0:
                    success_msg = f"✅ {model_type.upper()} Training Completed!"
                    self._log_console(success_msg)
                    self._data_collector.write_retrain_log(success_msg)
                    self._auto_apply_latest_model(model_type)
                else:
                    fail_msg = f"❌ {model_type.upper()} Training Failed (Exit Code: {process.returncode})."
                    tip_msg = "💡 Tip: Scroll up in this log box or run the script manually in terminal to see the full error."
                    self._log_console(fail_msg)
                    self._data_collector.write_retrain_log(fail_msg)
                    self._data_collector.write_retrain_log(tip_msg)
            except Exception as e:
                err_msg = f"❌ Error during retraining: {e}"
                self._log_console(err_msg)
                self._data_collector.write_retrain_log(err_msg)
            finally:
                self._data_collector.btn_retrain.configure(text="🔥 RETRAIN AI", state="normal")

        threading.Thread(target=run_train, daemon=True).start()

    def _auto_apply_latest_model(self, model_type: str) -> None:
        """Finds the latest version and applies it based on model type."""
        self._log_console(f"🔍 Scanning for the latest {model_type} model...")
        
        target_model = None
        target_secondary = None # Scaler for Keras, LabelEncoder for XGB
        
        if model_type == "keras":
            target_model = self._find_latest_file("posture_9_model_mix_paper", ".h5")
            target_secondary = self._find_latest_file("fsr_scaler_9_mix_paper", ".pkl")
        elif model_type == "random_forest":
            target_model = self._find_latest_file("posture_rf_model", ".pkl")
            target_secondary = "none"

        if target_model:
            self._model_mode.set(model_type)
            self._model_path_var.set(target_model)
            if target_secondary:
                self._scaler_path_var.set(target_secondary)
            
            self._on_model_mode_change() # Update UI state
            self._on_apply_model() # Apply to Fog Node
            self._log_console(f"🚀 AUTO-APPLIED {model_type.upper()}: {target_model}")
            messagebox.showinfo("AI Updated", f"{model_type.upper()} model updated:\n{target_model}")
        else:
            self._log_console(f"⚠️ Could not find latest {model_type} files.")

    def _find_latest_file(self, base_name: str, extension: str) -> Optional[str]:
        persistent_dir = paths.get_models_dir()
        bundled_dir = PROJECT_ROOT / "ai" / "models"
        
        files = []
        if persistent_dir.exists():
            files.extend(list(persistent_dir.glob(f"{base_name}_v*{extension}")))
        if bundled_dir.exists():
            files.extend(list(bundled_dir.glob(f"{base_name}_v*{extension}")))
            
        if not files: return None
        
        def get_v(path):
            match = re.search(r'_v(\d+)', path.name)
            return int(match.group(1)) if match else 0
        
        latest = max(files, key=get_v)
        try:
            return str(latest.relative_to(paths.DATA_ROOT))
        except ValueError:
            try:
                return str(latest.relative_to(PROJECT_ROOT))
            except ValueError:
                return str(latest)
        errors = []
        try:
            conf = float(self._smooth_conf_var.get().strip())
            if not (0.0 <= conf <= 1.0):
                raise ValueError
        except ValueError:
            errors.append("Min Confidence must be a number between 0.0 and 1.0")
            conf = None

        try:
            window = int(self._smooth_window_var.get().strip())
            if window < 1:
                raise ValueError
        except ValueError:
            errors.append("Window Size must be a whole number ≥ 1")
            window = None

        try:
            votes = int(self._smooth_votes_var.get().strip())
            if votes < 1:
                raise ValueError
        except ValueError:
            errors.append("Min Votes must be a whole number ≥ 1")
            votes = None

        if errors:
            self._smooth_status_lbl.configure(
                text="⚠️  " + "  |  ".join(errors), text_color=COLOR["red"]
            )
            return

        # Persist to LocalDB (survives restarts)
        self._db.set_config("min_confidence",       str(conf))
        self._db.set_config("smoothing_window_size", str(window))
        self._db.set_config("smoothing_min_votes",   str(votes))

        # Hot-update running Fog Node via MQTT (no restart needed)
        if self._mqtt_connected:
            payload = {"min_confidence": conf, "smoothing_window_size": window,
                       "smoothing_min_votes": votes}
            import json as _json
            self._mqtt_monitor._client.publish(
                "cushion/fog/config", _json.dumps(payload), qos=1
            )
            self._smooth_status_lbl.configure(
                text=f"✅ Applied: conf={conf:.0%} | window={window} | votes={votes}",
                text_color=COLOR["green"]
            )
            self._log_console(
                f"Smoothing updated — conf={conf:.0%}, window={window}, votes={votes}"
            )
        else:
            self._smooth_status_lbl.configure(
                text="ℹ️  Saved to DB. Will take effect on next Fog Node start.",
                text_color=COLOR["yellow"]
            )
            self._log_console("Smoothing saved to LocalDB (Fog Node not connected)")

    # ── Cloud Queue ──────────────────────────────────────────────────────

    def _build_cloud_queue_panel(self, parent) -> None:
        """Cloud Queue management panel shown in Config & Control view."""
        frame = ctk.CTkFrame(parent, fg_color=COLOR["surface"], corner_radius=12)
        frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(12, 16))
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame, text="OFFLINE CLOUD QUEUE",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLOR["muted"],
        ).grid(row=0, column=0, columnspan=4, padx=16, pady=(14, 6), sticky="w")

        # ── Status label ─────────────────────────────────────────────────
        self._queue_status_lbl = ctk.CTkLabel(
            frame, text="● 0 events pending",
            font=ctk.CTkFont(size=12),
            text_color=COLOR["green"],
        )
        self._queue_status_lbl.grid(row=1, column=0, columnspan=4, padx=16, pady=(0, 6), sticky="w")

        # ── Retention row ─────────────────────────────────────────────────
        ctk.CTkLabel(
            frame, text="Auto-delete after:",
            font=ctk.CTkFont(size=12), text_color=COLOR["text"],
        ).grid(row=2, column=0, padx=16, pady=4, sticky="w")

        default_days = self._db.get_config("cloud_queue_retention_days", "7")
        self._retention_var = ctk.StringVar(value=default_days)
        ctk.CTkEntry(
            frame, textvariable=self._retention_var,
            width=60, height=30,
            font=ctk.CTkFont(size=12),
        ).grid(row=2, column=1, padx=(4, 4), pady=4, sticky="w")

        ctk.CTkLabel(
            frame, text="days",
            font=ctk.CTkFont(size=12), text_color=COLOR["muted"],
        ).grid(row=2, column=2, padx=(0, 8), pady=4, sticky="w")

        ctk.CTkButton(
            frame, text="Save", width=64, height=30,
            fg_color=COLOR["surface"], border_width=1,
            border_color=COLOR["blue"], text_color=COLOR["blue"],
            hover_color="#1a2537",
            command=self._on_save_retention,
        ).grid(row=2, column=3, padx=(0, 16), pady=4, sticky="w")

        # ── Action buttons ────────────────────────────────────────────────
        ctk.CTkButton(
            frame, text="🗑 Clear All Pending",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#cf222e", hover_color="#a40e26",
            text_color="#ffffff", height=32,
            command=self._on_clear_cloud_queue,
        ).grid(row=3, column=0, columnspan=4, padx=16, pady=(4, 14), sticky="w")

    def _on_save_retention(self) -> None:
        """Save the retention days setting to LocalDB."""
        try:
            days = int(self._retention_var.get().strip())
            if days < 1:
                raise ValueError
            self._db.set_config("cloud_queue_retention_days", str(days))
            self._log_console(f"Cloud queue retention set to {days} days")
        except ValueError:
            self._log_console("⚠️ Invalid retention value – enter a whole number ≥ 1")

    def _on_clear_cloud_queue(self) -> None:
        """Delete all pending events from the local queue."""
        from tkinter import messagebox
        count = self._db.get_pending_count()
        if count == 0:
            self._log_console("Cloud queue is already empty")
            return
        if messagebox.askyesno(
            "Clear Cloud Queue",
            f"Delete all {count} pending events?\nThis cannot be undone.",
        ):
            deleted = self._db.purge_all()
            self._log_console(f"🗑 Cleared {deleted} pending cloud events")
            self._update_queue_stats()

    def _update_queue_stats(self) -> None:
        """Refresh the queue status label (called from _poll)."""
        if not hasattr(self, "_queue_status_lbl"):
            return
        count = self._db.get_pending_count()
        if count == 0:
            self._queue_status_lbl.configure(
                text="● 0 events pending", text_color=COLOR["green"]
            )
        else:
            age = self._db.get_oldest_pending_age_hours()
            age_str = f"{age:.0f}h ago" if age is not None else "unknown"
            self._queue_status_lbl.configure(
                text=f"● {count} events pending  (oldest: {age_str})",
                text_color=COLOR["yellow"],
            )

    # ── Data Monitor ──────────────────────────────────────────────────────────

    def _build_monitor_panel(self, parent) -> None:

        frame = ctk.CTkFrame(parent, fg_color=COLOR["surface"], corner_radius=12)
        frame.grid(row=0, column=0, sticky="nsew", padx=16, pady=(12, 0))
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        # Title bar
        title_bar = ctk.CTkFrame(frame, fg_color="transparent")
        title_bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        # Empty column 5 acts as a spacer that pushes controls to the right
        title_bar.grid_columnconfigure(5, weight=1)

        ctk.CTkLabel(
            title_bar, text="DATA MONITOR",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLOR["muted"],
        ).grid(row=0, column=0, sticky="w", padx=(0, 20))

        # Channel selector (dropdown menu)
        self._label_to_key = {ch["label"]: ch["key"] for ch in CHANNELS}
        self._key_to_label = {ch["key"]: ch["label"] for ch in CHANNELS}
        
        self._channel_menu_var = ctk.StringVar(value=CHANNELS[0]["label"])
        self._channel_menu = ctk.CTkOptionMenu(
            title_bar,
            values=[ch["label"] for ch in CHANNELS],
            variable=self._channel_menu_var,
            width=200, height=30,
            font=ctk.CTkFont(size=12),
            fg_color=COLOR["bg"],
            button_color=COLOR["surface"],
            button_hover_color=COLOR["muted"],
            command=self._on_channel_menu_change,
        )
        self._channel_menu.grid(row=0, column=1, padx=4, sticky="w")

        # Control buttons (right side)
        ctrl = ctk.CTkFrame(title_bar, fg_color="transparent")
        ctrl.grid(row=0, column=6, padx=(12, 0), sticky="e")

        self._pause_btn = ctk.CTkButton(
            ctrl, text="⏸ Pause", width=80, height=28,
            font=ctk.CTkFont(size=11),
            fg_color=COLOR["surface"],
            border_width=1, border_color=COLOR["yellow"],
            text_color=COLOR["yellow"],
            hover_color=COLOR["bg"],
            command=self._on_toggle_pause,
        )
        self._pause_btn.grid(row=0, column=0, padx=4)

        ctk.CTkButton(
            ctrl, text="🗑 Clear", width=80, height=28,
            font=ctk.CTkFont(size=11),
            fg_color=COLOR["surface"],
            border_width=1, border_color=COLOR["muted"],
            text_color=COLOR["muted"],
            hover_color=COLOR["bg"],
            command=self._on_clear_monitor,
        ).grid(row=0, column=1, padx=4)

        # Text display area
        self._monitor_panels = {}
        container = ctk.CTkFrame(frame, fg_color="transparent")
        container.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 14))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        for ch in CHANNELS:
            panel = ctk.CTkTextbox(
                container,
                font=ctk.CTkFont(family="Courier", size=11),
                fg_color=COLOR["bg"],
                text_color=COLOR["text"],
                corner_radius=8,
                state="disabled",
                wrap="none",
            )
            panel.grid(row=0, column=0, sticky="nsew")
            panel.grid_remove()
            self._monitor_panels[ch["key"]] = panel

        # Activate first tab
        self._select_channel(CHANNELS[0]["key"])

    # ── Console ───────────────────────────────────────────────────────────────

    def _build_console_panel(self, parent) -> None:
        frame = ctk.CTkFrame(parent, fg_color=COLOR["surface"], corner_radius=12)
        frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(12, 16))
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            frame, text="CONSOLE LOG",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLOR["muted"],
        ).grid(row=0, column=0, padx=16, pady=(14, 4), sticky="w")

        self._console = ctk.CTkTextbox(
            frame,
            font=ctk.CTkFont(family="Courier", size=11),
            fg_color=COLOR["bg"],
            text_color=COLOR["muted"],
            corner_radius=8,
            state="disabled",
            height=130,
        )
        self._console.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 14))

    # =========================================================================
    # Event handlers
    # =========================================================================

    def _on_start(self) -> None:
        self._start_btn.configure(state="disabled", text="Starting…")
        self._stop_btn.configure(state="normal")
        self._monitors_started = False   # Reset so monitors re-attach on next Start
        
        # Read ngrok token from .env
        ngrok_token = _read_env("NGROK_AUTHTOKEN", "").strip()
        self._docker.start(authtoken=ngrok_token if ngrok_token else None)

    def _on_rebuild(self) -> None:
        if messagebox.askyesno("Confirm Rebuild", "Rebuilding will update libraries and core code. It may take 1-2 minutes. Continue?"):
            self._docker.rebuild()

    def _on_stop(self) -> None:
        self._stop_btn.configure(state="disabled", text="Stopping…")
        self._stop_monitors()
        self._docker.stop()
        self.after(4000, lambda: self._start_btn.configure(state="normal", text="▶  Start Services"))
        self.after(4000, lambda: self._stop_btn.configure(text="■  Stop Services"))

    def _start_monitors(self) -> None:
        """(Re)start all monitors. Safe to call multiple times – guarded by _monitors_started."""
        if self._monitors_started:
            return
        self._monitors_started = True
        self._mqtt_monitor.stop()
        self._ws_monitor.stop()
        self._mqtt_monitor.start()
        self._ws_monitor.start()
        
        self._mqtt_connected = False
        self._ws_connected = False
        self._success_logged = False
        # self._log_console("✅ Monitors attached to running services")

    def _stop_monitors(self) -> None:
        if not self._monitors_started:
            return
        self._monitors_started = False
        self._mqtt_monitor.stop()
        self._ws_monitor.stop()
        self._log_console("⚠️ Monitors detached (services disconnected)")

    def _on_model_mode_change(self) -> None:
        mode = self._model_mode.get()

        # Models that require a .keras file + a .pkl scaler
        KERAS_WITH_SCALER = {"fnn", "tiny_cnn", "resnet", "keras"}
        # Models that use only a .pkl file, no separate scaler
        PKL_NO_SCALER = {"random_forest"}

        if mode in KERAS_WITH_SCALER:
            label_text = {
                "keras":    "Model CNN Legacy (.h5):",
                "fnn":      "Model FNN (.keras):",
                "tiny_cnn": "Model Tiny CNN (.keras):",
                "resnet":   "Model ResNet (.keras):",
            }.get(mode, "Model (.keras):")
            self._model_lbl.configure(text=label_text)
            self._model_entry.configure(state="normal")
            self._browse_btn.configure(state="normal")

            # Show scaler elements
            if hasattr(self, "_scaler_lbl"):
                self._scaler_lbl.grid(row=4, column=0, padx=16, pady=4, sticky="e")
            if hasattr(self, "_scaler_entry"):
                self._scaler_entry.grid(row=4, column=1, columnspan=2, padx=8, pady=4, sticky="ew")
                self._scaler_entry.configure(state="normal")
            if hasattr(self, "_scaler_browse_btn"):
                self._scaler_browse_btn.grid(row=4, column=3, padx=(0, 8), pady=4)
                self._scaler_browse_btn.configure(state="normal")

            self._model_match_label.configure(text="")

        elif mode in PKL_NO_SCALER:
            self._model_lbl.configure(text="Model (.pkl):")
            self._model_entry.configure(state="normal")
            self._browse_btn.configure(state="normal")

            # Hide scaler elements
            if hasattr(self, "_scaler_lbl"):
                self._scaler_lbl.grid_forget()
            if hasattr(self, "_scaler_entry"):
                self._scaler_entry.grid_forget()
            if hasattr(self, "_scaler_browse_btn"):
                self._scaler_browse_btn.grid_forget()

            self._model_match_label.configure(
                text=f"ℹ️ Random Forest does not require a Scaler file.",
                text_color=COLOR["blue"]
            )

    def _on_browse_model(self) -> None:
        mode = self._model_mode.get()
        is_pickle = (mode == "random_forest")
        ftypes = [("Pickle Model", "*.pkl"), ("All Files", "*")] if is_pickle else [("Model File", "*.h5 *.keras"), ("All Files", "*")]
        
        mode_title = {
            "keras": "CNN Legacy (.h5)",
            "fnn": "Hybrid FNN (.keras)",
            "tiny_cnn": "Tiny CNN (.keras)",
            "resnet": "Micro ResNet (.keras)",
            "random_forest": "Random Forest (.pkl)"
        }.get(mode, mode.replace('_', ' ').title())
        
        title = f"Select {mode_title}"
        
        path = filedialog.askopenfilename(
            title=title,
            filetypes=ftypes,
            initialdir=str(paths.get_models_dir()),
        )
        if path:
            self._model_path_var.set(path)
            # Auto-detect paired scaler
            matched = self._guess_paired_scaler(path)
            if matched:
                self._scaler_path_var.set(matched)
                from pathlib import Path as _Path
                self._model_match_label.configure(
                    text=f"✅ Scaler auto-matched: {_Path(matched).name}",
                    text_color=COLOR["green"],
                )
            else:
                self._model_match_label.configure(
                    text="⚠️ No matching scaler found — please browse for .pkl manually",
                    text_color=COLOR["yellow"],
                )

    def _on_browse_scaler(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Scaler (.pkl)",
            filetypes=[("Pickle Scaler", "*.pkl"), ("All Files", "*")],
            initialdir=str(paths.get_models_dir()),
        )
        if path:
            self._scaler_path_var.set(path)
            from pathlib import Path as _Path
            self._model_match_label.configure(
                text=f"📦 Scaler manually set: {_Path(path).name}",
                text_color=COLOR["blue"],
            )

    def _guess_paired_scaler(self, h5_path: str) -> Optional[str]:
        """
        Try to auto-detect the matching .pkl scaler for a selected model.
        """
        h5 = Path(h5_path)
        model_dir = h5.parent
        pkl_files = list(model_dir.glob("*.pkl"))

        if not pkl_files:
            return None
        if len(pkl_files) == 1:
            return str(pkl_files[0])

        stem = h5.stem.lower()

        # 1. Match by version suffix (e.g. "_v1", "_v2", etc.)
        import re
        version_match = re.search(r'_v(\d+)', stem)
        if version_match:
            version_str = version_match.group(0)  # e.g. "_v1"
            
            # Extract type keywords
            model_type = None
            for t in ["tiny_cnn", "resnet", "fnn", "cnn"]:
                if t in stem:
                    model_type = t
                    break
            
            # Find candidate pkls containing the same version string
            candidates = [p for p in pkl_files if version_str in p.name.lower()]
            if len(candidates) == 1:
                return str(candidates[0])
            elif len(candidates) > 1 and model_type:
                # Filter candidates by type keyword
                type_keywords = [model_type]
                if model_type == "tiny_cnn":
                    type_keywords.append("cnn") # for backward compatibility
                
                type_matched = [p for p in candidates if any(kw in p.name.lower() for kw in type_keywords)]
                if len(type_matched) == 1:
                    return str(type_matched[0])
                elif len(type_matched) > 1:
                    best_match = [p for p in type_matched if model_type in p.name.lower()]
                    if best_match:
                        return str(best_match[0])
                    return str(type_matched[0])

        # 2. Match by variant marker suffix (legacy fallback)
        variant = None
        for marker in ["_model_", "_model"]:
            if marker in stem:
                variant = stem.split(marker, 1)[1]
                break

        if variant:
            for pkl in pkl_files:
                if variant.lower() in pkl.stem.lower():
                    return str(pkl)

        return None

    def _on_apply_model(self) -> None:
        mode = self._model_mode.get()
        ALL_MODES = {"keras", "random_forest", "fnn", "tiny_cnn", "resnet"}
        SCALER_REQUIRED = {"keras", "fnn", "tiny_cnn", "resnet"}

        if mode not in ALL_MODES:
            return

        model_path  = self._model_path_var.get().strip()
        scaler_path = self._scaler_path_var.get().strip() if mode in SCALER_REQUIRED else "none"

        # Validate model file using dynamic resolution
        resolved_model = paths.resolve_model_path(model_path)
        if not Path(resolved_model).exists():
            messagebox.showwarning(
                "Model Not Found",
                f"Model file not found:\n{model_path}\n\nPlease browse for a valid model file.",
            )
            return

        # Validate scaler file
        resolved_scaler = paths.resolve_model_path(scaler_path) if scaler_path != "none" else ""
        if mode in SCALER_REQUIRED and not Path(resolved_scaler).exists():
            messagebox.showwarning(
                "Scaler Not Found",
                f"Scaler file not found:\n{scaler_path}\n\nPlease browse for the matching .pkl file.",
            )
            return

        # Convert to relative paths for portability
        try:
            model_path = str(Path(model_path).relative_to(paths.DATA_ROOT))
        except ValueError:
            try:
                model_path = str(Path(model_path).relative_to(PROJECT_ROOT))
            except ValueError:
                pass

        try:
            if scaler_path != "none":
                scaler_path = str(Path(scaler_path).relative_to(paths.DATA_ROOT))
        except ValueError:
            try:
                if scaler_path != "none":
                    scaler_path = str(Path(scaler_path).relative_to(PROJECT_ROOT))
            except ValueError:
                pass

        # Persist to DB (sole source of truth for model paths)
        self._db.set_config("model_type",  mode)
        self._db.set_config("model_path",  model_path)
        self._db.set_config("scaler_path", scaler_path)
        self._log_console(f"Config saved → [{mode}] {model_path}")

        # Hot-reload if MQTT is live (no Docker restart needed!)
        if self._mqtt_connected:
            ok = self._mqtt_monitor.publish_model_reload(mode, model_path, scaler_path)
            if ok:
                self._log_console("🔥 Hot-reload sent — model will swap in ~2s without restart")
                self._apply_btn.configure(text="🔥 Sent! (no restart needed)")
                self.after(3000, lambda: self._apply_btn.configure(text="Apply & Restart Fog Node"))
                return

        # Fallback: MQTT not connected → restart fog node
        self._log_console("⚙️ MQTT offline — restarting Fog Node to apply model…")
        self._docker.restart_fog_node()


    def _on_channel_menu_change(self, selected_label: str) -> None:
        key = self._label_to_key.get(selected_label)
        if key:
            self._select_channel(key)

    def _select_channel(self, channel_key: str) -> None:
        self._active_channel = channel_key
        
        # Ensure dropdown matches (if called programmatically)
        label = self._key_to_label.get(channel_key)
        if label and hasattr(self, "_channel_menu_var"):
            self._channel_menu_var.set(label)
            
        for ch in CHANNELS:
            k = ch["key"]
            panel = self._monitor_panels[k]
            if k == channel_key:
                panel.grid()
            else:
                panel.grid_remove()

    def _on_toggle_pause(self) -> None:
        self._monitor_paused = not self._monitor_paused
        if self._monitor_paused:
            self._pause_btn.configure(text="▶ Resume", border_color=COLOR["green"], text_color=COLOR["green"])
        else:
            self._pause_btn.configure(text="⏸ Pause", border_color=COLOR["yellow"], text_color=COLOR["yellow"])

    def _on_clear_monitor(self) -> None:
        for pnl in self._monitor_panels.values():
            pnl.configure(state="normal")
            pnl.delete("1.0", "end")
            pnl.configure(state="disabled")

    # =========================================================================
    # Callbacks from background threads
    # =========================================================================

    def _on_docker_status(self, status: ServiceStatus) -> None:
        """Called from DockerManager's polling thread – put in queue for UI."""
        self._current_status = status
        # Auto-start monitors once BOTH services are healthy
        if (
            status.mosquitto == ServiceState.RUNNING
            and status.fog_node == ServiceState.RUNNING
        ):
            if not self._monitors_started:
                self.after(0, self._start_monitors)
                # Also trigger immediate discovery report so Ngrok IP is sent ASAP
                self.after(1000, self.report_discovery_ip) 
        else:
            if self._monitors_started:
                self.after(0, self._stop_monitors)

    # =========================================================================
    # Periodic UI update (runs on main thread)
    # =========================================================================

    def _start_poll(self) -> None:
        self.after(self.POLL_MS, self._poll)

    def _poll(self) -> None:
        # ── Consume log queue ────────────────────────────────────────────
        try:
            while True:
                line = self._log_queue.get_nowait()
                self._log_console(line)
        except queue.Empty:
            pass

        # ── Consume message queue ────────────────────────────────────────
        if not self._monitor_paused:
            try:
                count = 0
                while count < 20:  # Batch up to 20 per frame to stay smooth
                    msg: MonitorMessage = self._msg_queue.get_nowait()
                    self._append_monitor(msg)
                    self._total_msgs += 1
                    count += 1
            except queue.Empty:
                pass
            if hasattr(self, "_lbl_msg_count"):
                self._lbl_msg_count.configure(text=str(self._total_msgs))

        # ── Update status indicators ─────────────────────────────────────
        if self._current_status:
            self._update_status_ui(self._current_status)

        # Update cloud queue stats every ~5s (every 20 poll cycles × 250ms)
        if not hasattr(self, "_queue_poll_count"):
            self._queue_poll_count = 0
        self._queue_poll_count += 1
        if self._queue_poll_count >= 20:
            self._queue_poll_count = 0
            self._update_queue_stats()

        self.after(self.POLL_MS, self._poll)

    def _update_status_ui(self, status: ServiceStatus) -> None:
        def _state_style(state: ServiceState) -> tuple[str, str]:
            """Return (color, label) for a ServiceState."""
            return {
                ServiceState.RUNNING:  (COLOR["green"],  "Running"),
                ServiceState.STARTING: (COLOR["yellow"], "Starting…"),
                ServiceState.STOPPED:  (COLOR["muted"],  "Stopped"),
                ServiceState.ERROR:    (COLOR["red"],    "Error"),
                ServiceState.UNKNOWN:  (COLOR["muted"],  "Unknown"),
            }.get(state, (COLOR["muted"], "Unknown"))

        m_color, m_label = _state_style(status.mosquitto)
        f_color, f_label = _state_style(status.fog_node)

        self._dot_mosquitto.configure(text_color=m_color)
        self._lbl_mosquitto.configure(text=m_label, text_color=m_color)
        self._dot_fog.configure(text_color=f_color)
        self._lbl_fog.configure(text=f_label, text_color=f_color)

        # Header status
        if status.mosquitto == ServiceState.RUNNING and status.fog_node == ServiceState.RUNNING:
            self._header_status.configure(text="● All services running", text_color=COLOR["green"])
        elif ServiceState.ERROR in (status.mosquitto, status.fog_node):
            self._header_status.configure(text="● Service error", text_color=COLOR["red"])
        elif ServiceState.STARTING in (status.mosquitto, status.fog_node):
            self._header_status.configure(text="● Starting up…", text_color=COLOR["yellow"])
        else:
            self._header_status.configure(text="● Services stopped", text_color=COLOR["muted"])

    # =========================================================================
    # Helpers
    # =========================================================================

    def _log_console(self, text: str) -> None:
        """Append a line to the console log widget."""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}]  {text}\n"
        self._console.configure(state="normal")
        self._console.insert("end", line)
        self._console.see("end")
        
        # Trim if too long (optimized for Windows)
        if not hasattr(self, "_log_insert_count"):
            self._log_insert_count = 0
        self._log_insert_count += 1
        
        if self._log_insert_count > 20:
            self._log_insert_count = 0
            lines = int(self._console.index("end").split(".")[0])
            if lines > self.MAX_LOG + 20:
                self._console.delete("1.0", f"{lines - self.MAX_LOG}.0")
                
        self._console.configure(state="disabled")
        
        # Track connection success
        if "MQTT monitor connected" in text:
            self._mqtt_connected = True
        if "WebSocket monitor connected" in text:
            self._ws_connected = True
            
        if self._mqtt_connected and self._ws_connected and not self._success_logged:
            self._success_logged = True
            self.after(500, lambda: self._log_console("🚀 SYSTEM READY: All services are up and connected successfully!"))

    def _append_monitor(self, msg: MonitorMessage) -> None:
        """Append a MonitorMessage to the correct channel widget."""
        import json
        
        if msg.channel == "fog_to_app":
            try:
                payload_dict = json.loads(msg.payload)
                self._dashboard.update_data(payload_dict)
                self._data_collector.process_data(payload_dict)
                
                # Update AI Predictions tab with a human-readable summary
                ai_pnl = self._monitor_panels.get("ai_results")
                if ai_pnl:
                    posture_stable = payload_dict.get("posture", "Unknown")
                    posture_raw = payload_dict.get("posture_raw", "Unknown")
                    top3 = payload_dict.get("posture_top3", [])
                    
                    # 1. Header with Raw AI Opinion
                    line = f"[{msg.timestamp}] 🧠 AI OPINION: {posture_raw.upper()}\n"
                    
                    # 2. Show candidates
                    for i, item in enumerate(top3):
                        label_str = item.get("label", "???").upper()
                        c_val = item.get("confidence", 0.0)
                        prefix = "  ├─ " if i == 0 else "     "
                        line += f"{prefix}{i+1}. {label_str:<6} ({c_val:.2%})"
                        if label_str == posture_raw.upper():
                            line += " ⚡" # Flash for raw frame hit
                        line += "\n"
                    
                    # 3. Show Smoothing Status
                    window_size = int(self._smooth_window_var.get())
                    min_votes = int(self._smooth_votes_var.get())
                    
                    # Calculate votes for the stable posture in the current window (approx)
                    # For a perfect UI we'd need to send the window counts, but we can 
                    # derive if it's currently stable.
                    status_icon = "✅" if posture_raw.upper() == posture_stable.upper() else "⏳"
                    line += f"  {status_icon} STABLE: {posture_stable.upper()}\n"
                    line += "-" * 40 + "\n"
                    
                    ai_pnl.configure(state="normal")
                    ai_pnl.insert("end", line)
                    ai_pnl.see("end")
                    # Trim
                    ai_lines = int(ai_pnl.index("end").split(".")[0])
                    if ai_lines > self.MAX_LOG:
                        ai_pnl.delete("1.0", f"{ai_lines - self.MAX_LOG}.0")
                    ai_pnl.configure(state="disabled")
            except Exception:
                pass

        # Append raw string to monitor text box
        pnl = self._monitor_panels.get(msg.channel)
        if pnl:
            pnl.configure(state="normal")
            pnl.insert("end", f"[{msg.timestamp}]  {msg.payload}\n\n")
            pnl.see("end")
            
            # Optimization: only trim periodically to prevent Windows UI lag
            if not hasattr(pnl, "_insert_count"):
                pnl._insert_count = 0
            pnl._insert_count += 1
            
            if pnl._insert_count > 50:
                pnl._insert_count = 0
                lines = int(pnl.index("end").split(".")[0])
                if lines > self.MAX_LOG + 50:
                    pnl.delete("1.0", f"{lines - self.MAX_LOG}.0")
            
            pnl.configure(state="disabled")

    def report_discovery_ip(self):
        """Reports connection info to Firebase (Ngrok URL or Local IP)."""
        try:
            # 1. Try to get Ngrok URL first
            ngrok_url = self._docker.get_ngrok_url()
            
            # 2. Get Host LAN IP (as fallback)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 1))
            local_ip = s.getsockname()[0]
            s.close()
            
            # 3. Get Public IP
            public_ip = None
            try:
                public_ip = requests.get("https://api.ipify.org", timeout=5).text
            except:
                pass
                
            # 4. Determine final "local_ip" field to send
            # We overwrite local_ip with ngrok_url if available so Web App picks it up
            display_ip = ngrok_url if ngrok_url else local_ip

            # 5. Send to Firebase (only if configured)
            if not self.firebase_url.strip():
                return
                
            url = f"{self.firebase_url.rstrip('/')}/devices/{self.device_id}.json"
            payload = {
                "local_ip": local_ip,
                "ngrok_url": ngrok_url if ngrok_url else "",
                "public_ip": public_ip,
                "is_ngrok": bool(ngrok_url),
                "timestamp": int(time.time() * 1000)
            }
            requests.put(url, json=payload, timeout=5)
            
            if ngrok_url:
                self._log_queue.put(f"Discovery: Reported Ngrok URL {ngrok_url} to Firebase.")
            else:
                self._log_queue.put(f"Discovery: Reported LAN IP {local_ip} to Firebase.")
                
        except Exception as e:
            self._log_queue.put(f"Discovery Error: {e}")
            
        # Repeat every 5 minutes (cancel old timer if exists to avoid duplicates)
        if self._discovery_timer:
            self.after_cancel(self._discovery_timer)
        self._discovery_timer = self.after(300000, self.report_discovery_ip)

    def _on_close(self) -> None:
        """Clean up on window close."""
        self._mqtt_monitor.stop()
        self._ws_monitor.stop()
        self.destroy()
