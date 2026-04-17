"""
Data Collector Panel for the Smart Cushion Fog Node Launcher.
Allows users to collect raw sensor and AI data into an Excel file for model training or analysis.
"""
import json
import os
import datetime
from pathlib import Path
from typing import Dict, Any, List

import customtkinter as ctk
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
LABELS_FILE = PROJECT_ROOT / "launcher" / "saved_labels.json"
EXPORT_DIR = PROJECT_ROOT / "data_exports"

class DataCollectorPanel(ctk.CTkFrame):
    def __init__(self, master, fg_color="transparent"):
        super().__init__(master, fg_color=fg_color)
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Ensure directories exist
        os.makedirs(EXPORT_DIR, exist_ok=True)
        if not LABELS_FILE.parent.exists():
            os.makedirs(LABELS_FILE.parent, exist_ok=True)

        # ── State ──────────────────────────────────────────────────────────
        self.is_collecting = False
        self.collected_data: List[Dict[str, Any]] = []
        self.end_time = None
        self.total_duration_secs = 0
        self.current_label = ""
        self._after_id = None

        # ── Load Labels ───────────────────────────────────────────────────
        self.saved_labels = self._load_labels()
        if not self.saved_labels:
            self.saved_labels = ["Sitting straight", "Leaning left", "Leaning right", "Slouch forward", "Leaning back"]
            self._save_labels()

        # ── UI Construction ────────────────────────────────────────────────
        self._build_settings_panel()
        self._build_columns_panel()
        self._build_status_panel()

    def _load_labels(self) -> List[str]:
        if LABELS_FILE.exists():
            try:
                with open(LABELS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_labels(self):
        try:
            with open(LABELS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.saved_labels, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _build_settings_panel(self):
        frame = ctk.CTkFrame(self, corner_radius=12)
        frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="⚙️ COLLECTION SETTINGS", font=ctk.CTkFont(size=12, weight="bold"), text_color="gray").grid(row=0, column=0, columnspan=2, padx=16, pady=(14, 10), sticky="w")

        # Duration
        ctk.CTkLabel(frame, text="Duration (minutes):", font=ctk.CTkFont(size=13)).grid(row=1, column=0, padx=16, pady=6, sticky="w")
        self.duration_var = ctk.StringVar(value="30")
        self.duration_entry = ctk.CTkEntry(frame, textvariable=self.duration_var, width=100)
        self.duration_entry.grid(row=1, column=1, padx=16, pady=6, sticky="w")

        # Label Config
        ctk.CTkLabel(frame, text="Label:", font=ctk.CTkFont(size=13)).grid(row=2, column=0, padx=16, pady=6, sticky="w")
        self.label_var = ctk.StringVar(value=self.saved_labels[0] if self.saved_labels else "")
        self.label_combo = ctk.CTkComboBox(frame, values=self.saved_labels, variable=self.label_var, width=250)
        self.label_combo.grid(row=2, column=1, padx=16, pady=6, sticky="w")

        # Person Present Manual Override
        ctk.CTkLabel(frame, text="Person Present:", font=ctk.CTkFont(size=13)).grid(row=3, column=0, padx=16, pady=(6, 14), sticky="w")
        self.person_present_var = ctk.BooleanVar(value=True)
        self.person_present_cb = ctk.CTkCheckBox(frame, text="Current session has a person seated", variable=self.person_present_var)
        self.person_present_cb.grid(row=3, column=1, padx=16, pady=(6, 14), sticky="w")

    def _build_columns_panel(self):
        frame = ctk.CTkFrame(self, corner_radius=12)
        frame.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(frame, text="📊 EXCEL DATA COLUMNS", font=ctk.CTkFont(size=12, weight="bold"), text_color="gray").grid(row=0, column=0, columnspan=4, padx=16, pady=(14, 10), sticky="w")

        self.cb_vars = {}
        columns = [
            ("Time", "time", True),
            ("FSR Front Left", "fsr_front_left", True),
            ("FSR Front Mid", "fsr_front_mid", True),
            ("FSR Front Right", "fsr_front_right", True),
            ("FSR Mid Left", "fsr_mid_left", True),
            ("FSR Mid Mid", "fsr_mid_mid", True),
            ("FSR Mid Right", "fsr_mid_right", True),
            ("FSR Back Left", "fsr_back_left", True),
            ("FSR Back Mid", "fsr_back_mid", True),
            ("FSR Back Right", "fsr_back_right", True),
            ("Temperature", "temperature", True),
            ("Person Present", "person_present", True),
        ]

        row_idx = 1
        col_idx = 0
        for name, key, default_state in columns:
            var = ctk.BooleanVar(value=default_state)
            self.cb_vars[key] = var
            cb = ctk.CTkCheckBox(frame, text=name, variable=var)
            cb.grid(row=row_idx, column=col_idx, padx=16, pady=6, sticky="w")
            col_idx += 1
            if col_idx > 3:
                col_idx = 0
                row_idx += 1

        ctk.CTkLabel(frame, text="* Label column will always be included automatically.", font=ctk.CTkFont(size=11), text_color="gray").grid(row=row_idx+1, column=0, columnspan=4, padx=16, pady=(0, 14), sticky="w")

    def _build_status_panel(self):
        frame = ctk.CTkFrame(self, bg_color="transparent", fg_color="transparent")
        frame.grid(row=2, column=0, padx=20, pady=10, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)

        self.btn_action = ctk.CTkButton(
            frame, text="▶ START COLLECTION", font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#3fb950", hover_color="#2ea043", text_color="white", height=40,
            command=self._toggle_collection
        )
        self.btn_action.grid(row=0, column=0, pady=10)

        self.status_label = ctk.CTkLabel(frame, text="Ready to collect data...", font=ctk.CTkFont(size=14))
        self.status_label.grid(row=1, column=0, pady=5)

        self.count_label = ctk.CTkLabel(frame, text="Data rows: 0", font=ctk.CTkFont(size=12), text_color="gray")
        self.count_label.grid(row=2, column=0, pady=(0, 10))

        self.progress_bar = ctk.CTkProgressBar(frame, mode="determinate")
        self.progress_bar.set(0)
        self.progress_bar.grid(row=3, column=0, sticky="ew", padx=40)

    # ── Logic ──────────────────────────────────────────────────────────

    def _toggle_collection(self):
        if not self.is_collecting:
            self._start_collection()
        else:
            self._finish_collection()

    def _start_collection(self):
        try:
            dur_mins = float(self.duration_var.get().strip())
            if dur_mins <= 0:
                raise ValueError
        except ValueError:
            self.status_label.configure(text="Error: Invalid duration!", text_color="#f85149")
            return

        lbl = self.label_var.get().strip()
        if not lbl:
            self.status_label.configure(text="Error: Please enter a label!", text_color="#f85149")
            return

        # Save label to history if new
        if lbl not in self.saved_labels:
            self.saved_labels.append(lbl)
            self.label_combo.configure(values=self.saved_labels)
            self._save_labels()

        self.current_label = lbl
        self.collected_data.clear()
        
        self.total_duration_secs = int(dur_mins * 60)
        self.end_time = datetime.datetime.now() + datetime.timedelta(seconds=self.total_duration_secs)
        
        self.is_collecting = True
        
        # Disable UI
        self.duration_entry.configure(state="disabled")
        self.label_combo.configure(state="disabled")
        self.person_present_cb.configure(state="disabled")
        for cb in self.cb_vars.values(): # checkboxes are handled via var mostly, but visual disabled might be skipped for simplicity
            pass

        self.btn_action.configure(text="■ STOP", fg_color="#f85149", hover_color="#a40e26")
        self.status_label.configure(text="Collecting...", text_color="#3fb950")
        self.count_label.configure(text="Data rows: 0")
        self.progress_bar.set(0)

        self._tick_timer()

    def _tick_timer(self):
        if not self.is_collecting:
            return

        now = datetime.datetime.now()
        rem_secs = (self.end_time - now).total_seconds()
        
        if rem_secs <= 0:
            self._finish_collection()
            return

        # Update progress bar
        p = 1.0 - (rem_secs / self.total_duration_secs)
        self.progress_bar.set(p)

        # Update text
        m, s = divmod(int(rem_secs), 60)
        self.status_label.configure(text=f"Collecting... Time remaining: {m:02d}:{s:02d}")

        self._after_id = self.after(1000, self._tick_timer)

    def _finish_collection(self):
        self.is_collecting = False
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None
            
        self.duration_entry.configure(state="normal")
        self.label_combo.configure(state="normal")
        self.person_present_cb.configure(state="normal")
        
        self.btn_action.configure(text="▶ START COLLECTION", fg_color="#3fb950", hover_color="#2ea043")
        self.progress_bar.set(1.0)

        total_rows = len(self.collected_data)
        if total_rows == 0:
            self.status_label.configure(text="Stopped. No data to export.", text_color="gray")
            return

        self.status_label.configure(text="Exporting Excel file...", text_color="#58a6ff")
        self.update_idletasks() # Force UI refresh
        
        # Process and save Excel
        filepath = self._export_to_excel()
        if filepath:
            self.status_label.configure(text=f"Successfully saved: {filepath.name}", text_color="#3fb950")
        else:
            self.status_label.configure(text="Error saving Excel file!", text_color="#f85149")

    def _export_to_excel(self) -> Path:
        try:
            df = pd.DataFrame(self.collected_data)
            
            # Select columns map
            col_map = {
                "time": "Time",
                "fsr_front_left": "FSR Front Left",
                "fsr_front_mid": "FSR Front Mid",
                "fsr_front_right": "FSR Front Right",
                "fsr_mid_left": "FSR Mid Left",
                "fsr_mid_mid": "FSR Mid Mid",
                "fsr_mid_right": "FSR Mid Right",
                "fsr_back_left": "FSR Back Left",
                "fsr_back_mid": "FSR Back Mid",
                "fsr_back_right": "FSR Back Right",
                "temperature": "Temperature",
                "person_present": "Person Present",
                "label": "Label" # Always explicitly required
            }
            
            # Filter based on checkbox selection
            keep_cols = []
            for k, var in self.cb_vars.items():
                if var.get() == True:
                    keep_cols.append(k)
            keep_cols.append("label") # Always keep label

            # Only select available columns matching keep_cols
            available_cols = [c for c in keep_cols if c in df.columns]
            df = df[available_cols]

            # Rename columns to human readable string
            rename_dict = {c: col_map[c] for c in available_cols if c in col_map}
            df.rename(columns=rename_dict, inplace=True)

            # Format Boolean as 1/0 for AI training
            if "Person Present" in df.columns:
                df["Person Present"] = df["Person Present"].map({True: 1, False: 0})

            timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"cushion_data_{self.current_label.replace(' ', '_')}_{timestamp_str}.xlsx"
            filepath = EXPORT_DIR / filename

            df.to_excel(filepath, index=False)
            return filepath
        except Exception as e:
            print(f"Excel Export Error: {e}")
            return None

    def process_data(self, payload_dict: Dict[str, Any]):
        if not self.is_collecting:
            return

        try:
            row = {}
            row["time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            
            sensors = payload_dict.get("sensors", {})
            row["fsr_front_left"] = sensors.get("fsr_front_left", 0)
            row["fsr_front_mid"] = sensors.get("fsr_front_mid", 0)
            row["fsr_front_right"] = sensors.get("fsr_front_right", 0)
            row["fsr_mid_left"] = sensors.get("fsr_mid_left", 0)
            row["fsr_mid_mid"] = sensors.get("fsr_mid_mid", 0)
            row["fsr_mid_right"] = sensors.get("fsr_mid_right", 0)
            row["fsr_back_left"] = sensors.get("fsr_back_left", 0)
            row["fsr_back_mid"] = sensors.get("fsr_back_mid", 0)
            row["fsr_back_right"] = sensors.get("fsr_back_right", 0)
            row["temperature"] = sensors.get("temperature", 0.0)
            
            # Use manual toggle for data collection session (Human annotation)
            row["person_present"] = self.person_present_var.get()
            row["label"] = self.current_label
            
            self.collected_data.append(row)
            
            # Update UI counter every 10 items or so to avoid UI lag
            l = len(self.collected_data)
            if l % 10 == 0:
                self.count_label.configure(text=f"Data rows: {l}")
        except Exception:
            pass
