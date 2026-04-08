"""
Fog Node Launcher – Main Window (CustomTkinter GUI)

Cross-platform desktop application that provides a friendly GUI for:
  - Starting / stopping Docker Compose services
  - Selecting the AI model (PyTorch file or rule-based stub)
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
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from launcher.docker_manager import DockerManager, ServiceState, ServiceStatus
from launcher.mqtt_monitor import MQTTMonitor, MonitorMessage
from launcher.ws_monitor import WebSocketMonitor
from launcher.dashboard_panel import DashboardPanel
from launcher.data_collector_panel import DataCollectorPanel

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
]

PROJECT_ROOT = Path(__file__).parent.parent


def _read_env(key: str, default: str = "") -> str:
    """Read a single key from the project .env file."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return default
    for line in env_path.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def _write_env(key: str, value: str) -> None:
    """Update or add a key in the project .env file."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    lines  = env_path.read_text().splitlines()
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
    env_path.write_text("\n".join(new) + "\n")


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

        self._current_status: Optional[ServiceStatus] = None
        self._monitor_paused = False
        self._active_channel = CHANNELS[0]["key"]
        self._monitors_started = False   # Guard: start monitors only once per Start

        self._build_ui()
        self._start_poll()

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
        self._stop_btn.grid(row=8, column=0, padx=20, pady=(0, 20), sticky="ew")

    def _build_dashboard_view(self) -> None:
        self._view_dashboard = ctk.CTkFrame(self.main_content_frame, fg_color="transparent")
        self._view_dashboard.grid(row=0, column=0, sticky="nsew")
        self._view_dashboard.grid_rowconfigure(0, weight=1)
        self._view_dashboard.grid_columnconfigure(0, weight=1)
        
        self._dashboard_panel = DashboardPanel(self._view_dashboard)
        self._dashboard_panel.grid(row=0, column=0, sticky="nsew")
        self._dashboard = self._dashboard_panel

    def _build_config_view(self) -> None:
        self._view_config = ctk.CTkFrame(self.main_content_frame, fg_color="transparent")
        self._view_config.grid(row=0, column=0, sticky="nsew")
        self._view_config.grid_columnconfigure(0, weight=1)
        
        # System status panel
        row1 = ctk.CTkFrame(self._view_config, fg_color="transparent")
        row1.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        row1.grid_columnconfigure(0, weight=1)
        self._build_status_panel(row1)
        
        self._build_model_panel(self._view_config)

    def _build_monitor_view(self) -> None:
        self._view_monitor = ctk.CTkFrame(self.main_content_frame, fg_color="transparent")
        self._view_monitor.grid(row=0, column=0, sticky="nsew")
        self._view_monitor.grid_rowconfigure(0, weight=2)
        self._view_monitor.grid_rowconfigure(1, weight=1)
        self._view_monitor.grid_columnconfigure(0, weight=1)
        
        self._build_monitor_panel(self._view_monitor)
        self._build_console_panel(self._view_monitor)

    def _build_data_collector_view(self) -> None:
        self._view_collector = ctk.CTkFrame(self.main_content_frame, fg_color="transparent")
        self._view_collector.grid(row=0, column=0, sticky="nsew")
        self._view_collector.grid_rowconfigure(0, weight=1)
        self._view_collector.grid_columnconfigure(0, weight=1)
        
        self._data_collector = DataCollectorPanel(self._view_collector)
        self._data_collector.grid(row=0, column=0, sticky="nsew")

    def _select_nav(self, name: str) -> None:
        views = {
            "dashboard": self._view_dashboard,
            "collector": self._view_collector,
            "config": self._view_config,
            "monitor": self._view_monitor
        }
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
                     ).grid(row=3, column=0, columnspan=2, padx=16, pady=(6, 14), sticky="w")
        self._lbl_msg_count = ctk.CTkLabel(frame, text="0",
                                            text_color=COLOR["blue"],
                                            font=ctk.CTkFont(size=13, weight="bold"))
        self._lbl_msg_count.grid(row=3, column=2, padx=16)
        self._total_msgs = 0

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

        # Mode selector
        self._model_mode = ctk.StringVar(value="rule_based")

        ctk.CTkRadioButton(
            frame, text="Rule-based Stub  (no model needed)",
            variable=self._model_mode, value="rule_based",
            font=ctk.CTkFont(size=12),
            command=self._on_model_mode_change,
        ).grid(row=1, column=0, padx=16, pady=4, sticky="w")

        ctk.CTkRadioButton(
            frame, text="PyTorch Model  (.pt file)",
            variable=self._model_mode, value="pytorch",
            font=ctk.CTkFont(size=12),
            command=self._on_model_mode_change,
        ).grid(row=1, column=1, padx=8, pady=4, sticky="w")

        # File path entry
        self._model_path_var = ctk.StringVar(value=_read_env("MODEL_PATH", "ai/models/posture_model.pt"))
        self._model_entry = ctk.CTkEntry(
            frame,
            textvariable=self._model_path_var,
            font=ctk.CTkFont(family="Courier", size=11),
            state="disabled",
            corner_radius=6,
            height=32,
        )
        self._model_entry.grid(row=1, column=2, padx=8, pady=4, sticky="ew")

        self._browse_btn = ctk.CTkButton(
            frame, text="Browse…", width=80, height=32,
            fg_color=COLOR["surface"],
            border_color=COLOR["blue"],
            border_width=1,
            text_color=COLOR["blue"],
            hover_color="#1a2537",
            state="disabled",
            command=self._on_browse_model,
        )
        self._browse_btn.grid(row=1, column=3, padx=(0, 8), pady=4)

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
        self._apply_btn.grid(row=2, column=0, columnspan=4, padx=16, pady=(4, 14), sticky="w")

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
        self._docker.start()

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
        self._log_console("✅ Monitors attached to running services")

    def _stop_monitors(self) -> None:
        if not self._monitors_started:
            return
        self._monitors_started = False
        self._mqtt_monitor.stop()
        self._ws_monitor.stop()
        self._log_console("⚠️ Monitors detached (services disconnected)")

    def _on_model_mode_change(self) -> None:
        mode = self._model_mode.get()
        if mode == "pytorch":
            self._model_entry.configure(state="normal")
            self._browse_btn.configure(state="normal")
        else:
            self._model_entry.configure(state="disabled")
            self._browse_btn.configure(state="disabled")

    def _on_browse_model(self) -> None:
        path = filedialog.askopenfilename(
            title="Select PyTorch Model",
            filetypes=[("PyTorch Model", "*.pt *.pth"), ("All Files", "*")],
            initialdir=str(PROJECT_ROOT / "ai" / "models"),
        )
        if path:
            self._model_path_var.set(path)

    def _on_apply_model(self) -> None:
        mode = self._model_mode.get()
        if mode == "pytorch":
            model_path = self._model_path_var.get().strip()
            if not Path(model_path).exists() and not Path(PROJECT_ROOT / model_path).exists():
                messagebox.showwarning(
                    "Model Not Found",
                    f"The file was not found:\n{model_path}\n\nUsing rule-based mode instead.",
                )
                _write_env("MODEL_PATH", "ai/models/posture_model.pt")
            else:
                _write_env("MODEL_PATH", model_path)
            self._log_console(f"MODEL_PATH updated to: {model_path}")
        else:
            # Force model path to a non-existent file → triggers stub fallback
            _write_env("MODEL_PATH", "ai/models/posture_model.pt")
            self._log_console("AI Mode: Rule-based stub (model file will be ignored)")

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
        # Trim if too long
        lines = int(self._console.index("end").split(".")[0])
        if lines > self.MAX_LOG:
            self._console.delete("1.0", f"{lines - self.MAX_LOG}.0")
        self._console.configure(state="disabled")

    def _append_monitor(self, msg: MonitorMessage) -> None:
        """Append a MonitorMessage to the correct channel widget."""
        import json
        
        if msg.channel == "fog_to_app":
            try:
                payload_dict = json.loads(msg.payload)
                self._dashboard.update_data(payload_dict)
                self._data_collector.process_data(payload_dict)
            except Exception:
                pass

        pnl = self._monitor_panels.get(msg.channel)
        if pnl is None:
            return

        header = f"── {msg.timestamp}  {msg.topic} ──\n"
        body   = msg.payload + "\n\n"

        pnl.configure(state="normal")
        pnl.insert("end", header + body)
        pnl.see("end")
        # Trim
        lines = int(pnl.index("end").split(".")[0])
        if lines > self.MAX_LOG:
            pnl.delete("1.0", f"{lines - self.MAX_LOG}.0")
        pnl.configure(state="disabled")

    def _on_close(self) -> None:
        """Clean up on window close."""
        self._mqtt_monitor.stop()
        self._ws_monitor.stop()
        self.destroy()
