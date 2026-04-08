import re

def rewrite():
    with open("launcher/main_window.py", "r") as f:
        text = f.read()

    # 1. Update CHANNELS
    text = re.sub(
        r'CHANNELS = \[.*?\]', 
        '''CHANNELS = [
    {"key": "esp32_to_fog",  "label": "📡  ESP32 → Fog",   "color": COLOR["blue"]},
    {"key": "fog_to_esp32",  "label": "📤  Fog → ESP32",   "color": COLOR["red"]},
    {"key": "fog_to_cloud",  "label": "☁️   Fog → Cloud",  "color": COLOR["purple"]},
    {"key": "fog_to_app",    "label": "📱  Fog → App",     "color": COLOR["green"]},
]''', 
        text, 
        flags=re.DOTALL
    )

    # 2. Replace _build_ui through _build_service_control (Lines 153 to 241 approx)
    # We find where "_build_ui" starts, and where "_build_status_panel" starts
    start_idx = text.find('    def _build_ui(self) -> None:')
    end_idx = text.find('    def _build_status_panel(self, parent) -> None:')
    
    new_ui_core = '''    def _build_ui(self) -> None:
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

        self._select_nav("dashboard")

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=COLOR["surface"])
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(6, weight=1)

        title = ctk.CTkLabel(sidebar, text="🪑 Smart Cushion", font=ctk.CTkFont(size=20, weight="bold"))
        title.grid(row=0, column=0, padx=20, pady=(20, 10))

        self._header_status = ctk.CTkLabel(sidebar, text="● Services stopped", font=ctk.CTkFont(size=12), text_color=COLOR["muted"])
        self._header_status.grid(row=1, column=0, padx=20, pady=(0, 20))

        self._nav_buttons = {}
        
        def make_nav(name, text, row):
            btn = ctk.CTkButton(
                sidebar, text=text, fg_color="transparent", text_color=COLOR["text"], anchor="w",
                font=ctk.CTkFont(size=14, weight="bold"), command=lambda n=name: self._select_nav(n)
            )
            btn.grid(row=row, column=0, padx=20, pady=5, sticky="ew")
            self._nav_buttons[name] = btn

        make_nav("dashboard", "📊 Live Dashboard", 2)
        make_nav("config", "⚙️ Config & Control", 3)
        make_nav("monitor", "📋 Logs & Raw Data", 4)
        
        self._start_btn = ctk.CTkButton(
            sidebar, text="▶ Start Services", font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=COLOR["green"], hover_color="#2ea043", text_color="#ffffff", command=self._on_start
        )
        self._start_btn.grid(row=7, column=0, padx=20, pady=10, sticky="ew")

        self._stop_btn = ctk.CTkButton(
            sidebar, text="■ Stop Services", font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#cf222e", hover_color="#a40e26", text_color="#ffffff", state="disabled", command=self._on_stop
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

    def _select_nav(self, name: str) -> None:
        views = {"dashboard": self._view_dashboard, "config": self._view_config, "monitor": self._view_monitor}
        for k, v in views.items():
            if k == name:
                v.grid()
                self._nav_buttons[k].configure(fg_color="#1a2537", border_color=COLOR["blue"], border_width=1)
            else:
                v.grid_remove()
                self._nav_buttons[k].configure(fg_color="transparent", border_width=0)

'''
    
    text = text[:start_idx] + new_ui_core + text[end_idx:]

    # Fix _build_status_panel
    text = text.replace(
        'frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0))',
        'frame.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)'
    )
    
    # Fix _build_model_panel
    text = text.replace(
        'def _build_model_panel(self) -> None:\n        frame = ctk.CTkFrame(self',
        'def _build_model_panel(self, parent) -> None:\n        frame = ctk.CTkFrame(parent'
    )
    
    # Fix _build_monitor_panel
    text = text.replace(
        'def _build_monitor_panel(self) -> None:\n        frame = ctk.CTkFrame(self',
        'def _build_monitor_panel(self, parent) -> None:\n        frame = ctk.CTkFrame(parent'
    )
    text = text.replace(
        'frame.grid(row=3, column=0, sticky="nsew", padx=16, pady=(12, 0))',
        'frame.grid(row=0, column=0, sticky="nsew", padx=16, pady=(12, 0))'
    )
    
    # Fix _build_console_panel
    text = text.replace(
        'def _build_console_panel(self) -> None:\n        frame = ctk.CTkFrame(self',
        'def _build_console_panel(self, parent) -> None:\n        frame = ctk.CTkFrame(parent'
    )
    text = text.replace(
        'frame.grid(row=4, column=0, sticky="nsew", padx=16, pady=(12, 16))',
        'frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(12, 16))'
    )

    # Fix clean monitor and msg handler
    text = text.replace('''    def _on_clear_monitor(self) -> None:
        for k, pnl in self._monitor_panels.items():
            if k == "live_dashboard":
                continue
            pnl.configure(state="normal")
            pnl.delete("1.0", "end")
            pnl.configure(state="disabled")''', '''    def _on_clear_monitor(self) -> None:
        for pnl in self._monitor_panels.values():
            pnl.configure(state="normal")
            pnl.delete("1.0", "end")
            pnl.configure(state="disabled")''')

    text = text.replace('''        pnl = self._monitor_panels.get(msg.channel)
        if pnl is None or msg.channel == "live_dashboard":
            return''', '''        pnl = self._monitor_panels.get(msg.channel)
        if pnl is None:
            return''')

    with open("launcher/main_window.py", "w") as f:
        f.write(text)

if __name__ == "__main__":
    rewrite()

