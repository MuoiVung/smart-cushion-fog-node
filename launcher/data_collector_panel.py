"""
Data Collector Panel for the Smart Cushion Fog Node Launcher.
Allows users to collect raw sensor and AI data into an Excel file for model training or analysis.
"""
import json
import os
import datetime
from pathlib import Path
from typing import Dict, Any, List
import tkinter.filedialog as filedialog

import customtkinter as ctk
import pandas as pd

import utils.paths as paths

PROJECT_ROOT = paths.DATA_ROOT
LABELS_FILE = paths.get_labels_file()
EXPORT_DIR = paths.get_export_dir()

LABEL_MAP = {
    "EMPTY (Cushion is empty)": "EMPTY",
    "OBJECT (Non-human object)": "OBJECT",
    "NUP (Natural Upright Posture)": "NUP",
    "LF (Lean Forward)": "LF",
    "LB (Lean Backward)": "LB",
    "LFSR (Lean Forward-Support Right)": "LFSR",
    "LFSL (Lean Forward-Support Left)": "LFSL",
    "CRL (Cross-Right Legged)": "CRL",
    "CLL (Cross-Left Legged)": "CLL",
    "CRLL (Cross-Right Legged-Legged)": "CRLL",
    "CLLL (Cross-Left Legged-Legged)": "CLLL"
}

class DataCollectorPanel(ctk.CTkFrame):
    def __init__(self, master, fg_color="transparent", retrain_callback=None):
        super().__init__(master, fg_color=fg_color)
        # retrain_callback will now receive (model_type)
        self.retrain_callback = retrain_callback
        
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

        self.duration_hr_var = ctk.StringVar(value="0")
        self.duration_min_var = ctk.StringVar(value="30")
        self.duration_sec_var = ctk.StringVar(value="0")

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
        labels = list(LABEL_MAP.keys())
        if LABELS_FILE.exists():
            try:
                with open(LABELS_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    for s in saved:
                        if s not in labels:
                            labels.append(s)
            except Exception:
                pass
        return labels

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

        # Duration (HH:MM:SS)
        ctk.CTkLabel(frame, text="Duration (H:M:S):", font=ctk.CTkFont(size=13)).grid(row=1, column=0, padx=16, pady=6, sticky="w")
        
        dur_frame = ctk.CTkFrame(frame, fg_color="transparent")
        dur_frame.grid(row=1, column=1, padx=16, pady=6, sticky="w")
        
        self.duration_hr_entry = ctk.CTkEntry(dur_frame, textvariable=self.duration_hr_var, width=45)
        self.duration_hr_entry.grid(row=0, column=0, padx=(0, 2))
        ctk.CTkLabel(dur_frame, text=":").grid(row=0, column=1, padx=2)
        
        self.duration_min_entry = ctk.CTkEntry(dur_frame, textvariable=self.duration_min_var, width=45)
        self.duration_min_entry.grid(row=0, column=2, padx=2)
        ctk.CTkLabel(dur_frame, text=":").grid(row=0, column=3, padx=2)
        
        self.duration_sec_entry = ctk.CTkEntry(dur_frame, textvariable=self.duration_sec_var, width=45)
        self.duration_sec_entry.grid(row=0, column=4, padx=(2, 0))

        # Label Config
        ctk.CTkLabel(frame, text="Label:", font=ctk.CTkFont(size=13)).grid(row=2, column=0, padx=16, pady=6, sticky="w")
        self.label_var = ctk.StringVar(value=self.saved_labels[0] if self.saved_labels else "")
        self.label_combo = ctk.CTkComboBox(frame, values=self.saved_labels, variable=self.label_var, width=250)
        self.label_combo.grid(row=2, column=1, padx=16, pady=6, sticky="w")

        # Person Present Manual Override
        ctk.CTkLabel(frame, text="Person Present:", font=ctk.CTkFont(size=13)).grid(row=3, column=0, padx=16, pady=6, sticky="w")
        self.person_present_var = ctk.BooleanVar(value=True)
        self.person_present_cb = ctk.CTkCheckBox(frame, text="Current session has a person seated", variable=self.person_present_var)
        self.person_present_cb.grid(row=3, column=1, padx=16, pady=6, sticky="w")

        # Export Directory
        ctk.CTkLabel(frame, text="Export Folder:", font=ctk.CTkFont(size=13)).grid(row=4, column=0, padx=16, pady=(6, 14), sticky="w")
        self.export_dir_var = ctk.StringVar(value=str(EXPORT_DIR.resolve()))
        
        dir_frame = ctk.CTkFrame(frame, fg_color="transparent")
        dir_frame.grid(row=4, column=1, padx=16, pady=(6, 14), sticky="ew")
        dir_frame.grid_columnconfigure(0, weight=1)
        
        self.dir_entry = ctk.CTkEntry(dir_frame, textvariable=self.export_dir_var, state="readonly")
        self.dir_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        
        self.btn_browse = ctk.CTkButton(dir_frame, text="Browse...", width=80, command=self._browse_directory)
        self.btn_browse.grid(row=0, column=1)

        # Training Dataset Selection
        ctk.CTkLabel(frame, text="Train Dataset:", font=ctk.CTkFont(size=13)).grid(row=5, column=0, padx=16, pady=(6, 14), sticky="w")
        self.train_dataset_var = ctk.StringVar(value="All Folders")
        
        ds_frame = ctk.CTkFrame(frame, fg_color="transparent")
        ds_frame.grid(row=5, column=1, padx=16, pady=(6, 14), sticky="ew")
        ds_frame.grid_columnconfigure(0, weight=1)
        
        self.train_dataset_combo = ctk.CTkComboBox(
            ds_frame, 
            values=self._get_train_dataset_options(),
            variable=self.train_dataset_var,
        )
        self.train_dataset_combo.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        
        self.btn_refresh_ds = ctk.CTkButton(
            ds_frame, text="🔄", width=36, height=32,
            command=self.refresh_train_dataset_options
        )
        self.btn_refresh_ds.grid(row=0, column=1)



    def _get_train_dataset_options(self) -> List[str]:
        options = ["All Folders"]
        try:
            train_root = EXPORT_DIR.resolve()
            if train_root.exists():
                for p in train_root.iterdir():
                    if p.is_dir() and not p.name.startswith("."):
                        options.append(p.name)
        except Exception:
            pass
        return options

    def refresh_train_dataset_options(self):
        opts = self._get_train_dataset_options()
        if hasattr(self, "train_dataset_combo"):
            self.train_dataset_combo.configure(values=opts)
            if self.train_dataset_var.get() not in opts:
                self.train_dataset_var.set("All Folders")

    def _browse_directory(self):
        selected_dir = filedialog.askdirectory(initialdir=self.export_dir_var.get(), title="Select Export Folder")
        if selected_dir:
            self.export_dir_var.set(selected_dir)
            self.refresh_train_dataset_options()

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
            ("AI Prediction", "ai_prediction", True),
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

        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.grid(row=0, column=0, pady=10)

        self.btn_action = ctk.CTkButton(
            btn_frame, text="▶ START COLLECTION", font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#3fb950", hover_color="#2ea043", text_color="white", height=40,
            command=self._toggle_collection
        )
        self.btn_action.grid(row=0, column=0, padx=5)

        # Model type for retraining
        self.retrain_type_var = ctk.StringVar(value="CNN (Keras)")
        self.retrain_combo = ctk.CTkComboBox(
            btn_frame, 
            values=["CNN (Keras)", "Random Forest"],
            variable=self.retrain_type_var,
            width=140, height=40
        )
        self.retrain_combo.grid(row=0, column=1, padx=5)

        self.btn_retrain = ctk.CTkButton(
            btn_frame, text="🔥 RETRAIN AI", font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#d29922", hover_color="#b0801a", text_color="white", height=40,
            command=self._on_retrain_click
        )
        self.btn_retrain.grid(row=0, column=2, padx=5)

        self.status_label = ctk.CTkLabel(frame, text="Ready to collect data...", font=ctk.CTkFont(size=14))
        self.status_label.grid(row=1, column=0, pady=5)

        self.ai_prediction_label = ctk.CTkLabel(
            frame, 
            text="Live AI Prediction: None", 
            font=ctk.CTkFont(size=13, weight="bold"), 
            text_color="#58a6ff"
        )
        self.ai_prediction_label.grid(row=2, column=0, pady=(0, 5))

        self.count_label = ctk.CTkLabel(frame, text="Data rows: 0", font=ctk.CTkFont(size=12), text_color="gray")
        self.count_label.grid(row=3, column=0, pady=(0, 10))

        self.progress_bar = ctk.CTkProgressBar(frame, mode="determinate")
        self.progress_bar.set(0)
        self.progress_bar.grid(row=4, column=0, sticky="ew", padx=40)

        # ── AI Retraining Logs UI ──
        self.log_frame = ctk.CTkFrame(frame, corner_radius=12, fg_color="#161b22")
        self.log_frame.grid(row=5, column=0, padx=20, pady=(15, 5), sticky="nsew")
        self.log_frame.grid_columnconfigure(0, weight=1)
        self.log_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self.log_frame, 
            text="🤖 AI RETRAINING PROGRESS LOGS", 
            font=ctk.CTkFont(size=12, weight="bold"), 
            text_color="#d29922"
        ).grid(row=0, column=0, padx=16, pady=(10, 5), sticky="w")

        self.log_textbox = ctk.CTkTextbox(
            self.log_frame, 
            font=ctk.CTkFont(family="Courier", size=11),
            fg_color="#0d1117",
            text_color="#e6edf3",
            height=200,
            wrap="word",
            corner_radius=8
        )
        self.log_textbox.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="nsew")
        self.log_textbox.insert("end", "Waiting for AI retraining to start...\n")
        self.log_textbox.configure(state="disabled")

    def write_retrain_log(self, message: str) -> None:
        self.log_textbox.configure(state="normal")
        self.log_textbox.insert("end", message + "\n")
        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def clear_retrain_log(self) -> None:
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")

    def _on_retrain_click(self):
        if self.retrain_callback:
            mapping = {
                "CNN (Keras)": "keras",
                "Random Forest": "random_forest"
            }
            m_type = mapping.get(self.retrain_type_var.get(), "keras")
            
            selected_ds = self.train_dataset_var.get()
            train_root = EXPORT_DIR.resolve()
            if selected_ds == "All Folders":
                ds_path = str(train_root)
            else:
                ds_path = str(train_root / selected_ds)
                
            self.retrain_callback(m_type, ds_path)

    # ── Logic ──────────────────────────────────────────────────────────

    def _toggle_collection(self):
        if not self.is_collecting:
            self._start_collection()
        else:
            self._finish_collection()

    def _start_collection(self):
        try:
            hr = float(self.duration_hr_var.get().strip() or 0)
            mn = float(self.duration_min_var.get().strip() or 0)
            sc = float(self.duration_sec_var.get().strip() or 0)
            
            total_secs = int(hr * 3600 + mn * 60 + sc)
            if total_secs <= 0:
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

        # Map display name to abbreviation for Excel export
        self.current_label = LABEL_MAP.get(lbl, lbl)
        if "(" in lbl and ")" in lbl:
            self.current_posture_name = lbl.split("(")[-1].split(")")[0].strip()
        else:
            self.current_posture_name = lbl
        self.collected_data.clear()
        
        self.total_duration_secs = total_secs
        self.end_time = datetime.datetime.now() + datetime.timedelta(seconds=self.total_duration_secs)
        
        self.is_collecting = True
        
        # Disable UI
        self.duration_hr_entry.configure(state="disabled")
        self.duration_min_entry.configure(state="disabled")
        self.duration_sec_entry.configure(state="disabled")
        self.label_combo.configure(state="disabled")
        self.person_present_cb.configure(state="disabled")
        self.btn_browse.configure(state="disabled")

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
        h, rem = divmod(int(rem_secs), 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            self.status_label.configure(text=f"Collecting... Time remaining: {h:02d}:{m:02d}:{s:02d}")
        else:
            self.status_label.configure(text=f"Collecting... Time remaining: {m:02d}:{s:02d}")

        self._after_id = self.after(1000, self._tick_timer)

    def _finish_collection(self):
        self.is_collecting = False
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None
            
        self.duration_hr_entry.configure(state="normal")
        self.duration_min_entry.configure(state="normal")
        self.duration_sec_entry.configure(state="normal")
        self.label_combo.configure(state="normal")
        self.person_present_cb.configure(state="normal")
        self.btn_browse.configure(state="normal")
        
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
            self.refresh_train_dataset_options()
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
                "ai_prediction": "AI Prediction",
                "label": "Label", # Always explicitly required
                "posture_name": "Posture Name"
            }
            
            # Filter based on checkbox selection
            keep_cols = []
            for k, var in self.cb_vars.items():
                if var.get() == True:
                    keep_cols.append(k)
            keep_cols.append("label") # Always keep label
            keep_cols.append("posture_name")

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
            
            export_path = Path(self.export_dir_var.get())
            os.makedirs(export_path, exist_ok=True)
            filepath = export_path / filename

            df.to_excel(filepath, index=False)
            return filepath
        except Exception as e:
            print(f"Excel Export Error: {e}")
            return None

    def process_data(self, payload_dict: Dict[str, Any]):
        # Extract live AI prediction
        ai_posture = payload_dict.get("posture_raw", payload_dict.get("posture", "Unknown"))
        if hasattr(self, "ai_prediction_label"):
            self.ai_prediction_label.configure(text=f"Live AI Prediction: {ai_posture}")

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
            row["ai_prediction"] = ai_posture
            
            row["label"] = self.current_label
            row["posture_name"] = getattr(self, "current_posture_name", self.current_label)
            
            self.collected_data.append(row)
            
            # Update UI counter every 10 items or so to avoid UI lag
            l = len(self.collected_data)
            if l % 10 == 0:
                self.count_label.configure(text=f"Data rows: {l}")
        except Exception:
            pass
