"""
Live Dashboard Panel for the Smart Cushion Fog Node Launcher.
Displays the processed AI features, posture, and confidence in a graphical format.
"""
import json
import customtkinter as ctk
from typing import Dict, Any

class DashboardPanel(ctk.CTkFrame):
    def __init__(self, master, fg_color="transparent"):
        super().__init__(master, fg_color=fg_color)
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=1)
        
        # ── State ──────────────────────────────────────────────────────────
        self.posture_label = ctk.CTkLabel(
            self, text="Waiting for data...", 
            font=ctk.CTkFont(size=24, weight="bold")
        )
        self.posture_label.grid(row=0, column=0, columnspan=3, pady=(20, 10))
        
        self.confidence_label = ctk.CTkLabel(
            self, text="Confidence: --%", 
            font=ctk.CTkFont(size=14), text_color="gray"
        )
        self.confidence_label.grid(row=1, column=0, columnspan=3, pady=(0, 20))

        # ── Features Grid (Relative Percentages) ───────────────────────────
        features_frame = ctk.CTkFrame(self, fg_color="#161b22", corner_radius=12)
        features_frame.grid(row=2, column=0, columnspan=3, padx=40, sticky="ew")
        features_frame.grid_columnconfigure(0, weight=1)
        features_frame.grid_columnconfigure(1, weight=1)
        features_frame.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            features_frame, text="Processed AI Features (Relative Pressure %)",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="gray"
        ).grid(row=0, column=0, columnspan=3, pady=(10, 10))

        self.fl_bar = self._create_feature_bar(features_frame, "Front Left", 1, 0)
        self.fm_bar = self._create_feature_bar(features_frame, "Front Mid", 1, 1)
        self.fr_bar = self._create_feature_bar(features_frame, "Front Right", 1, 2)
        
        self.ml_bar = self._create_feature_bar(features_frame, "Mid Left", 2, 0)
        self.mm_bar = self._create_feature_bar(features_frame, "Mid Mid", 2, 1)
        self.mr_bar = self._create_feature_bar(features_frame, "Mid Right", 2, 2)

        self.bl_bar = self._create_feature_bar(features_frame, "Back Left", 3, 0)
        self.bm_bar = self._create_feature_bar(features_frame, "Back Mid", 3, 1)
        self.br_bar = self._create_feature_bar(features_frame, "Back Right", 3, 2)

    def _create_feature_bar(self, parent, name, row, col):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=col, padx=20, pady=10, sticky="ew")
        
        lbl = ctk.CTkLabel(frame, text=f"{name}: 0%", font=ctk.CTkFont(size=12))
        lbl.pack(anchor="w")
        
        progress = ctk.CTkProgressBar(frame, height=12, fg_color="#0d1117", progress_color="#58a6ff")
        progress.set(0)
        progress.pack(fill="x", pady=(5, 0))
        
        return {"lbl": lbl, "progress": progress}

    def update_data(self, payload_dict: Dict[str, Any]):
        try:
            detected = payload_dict.get("person_detected", False)
            if not detected:
                self.posture_label.configure(text="No Person Detected", text_color="gray")
                self.confidence_label.configure(text="Confidence: --%")
                self._update_bar(self.fl_bar, "Front Left", 0.0)
                self._update_bar(self.fm_bar, "Front Mid", 0.0)
                self._update_bar(self.fr_bar, "Front Right", 0.0)
                self._update_bar(self.ml_bar, "Mid Left", 0.0)
                self._update_bar(self.mm_bar, "Mid Mid", 0.0)
                self._update_bar(self.mr_bar, "Mid Right", 0.0)
                self._update_bar(self.bl_bar, "Back Left", 0.0)
                self._update_bar(self.bm_bar, "Back Mid", 0.0)
                self._update_bar(self.br_bar, "Back Right", 0.0)
                return

            posture = payload_dict.get("posture", "UNKNOWN").upper()
            confidence = payload_dict.get("confidence", 0.0)
            
            # Color logic
            if posture == "CORRECT":
                color = "#3fb950"  # Green
            elif posture == "UNKNOWN":
                color = "gray"
            else:
                color = "#f85149"  # Red
                
            self.posture_label.configure(text=f"Posture: {posture}", text_color=color)
            self.confidence_label.configure(text=f"Confidence: {int(confidence * 100)}%")

            features = payload_dict.get("features")
            if features and len(features) == 9:
                self._update_bar(self.fl_bar, "Front Left", features[0])
                self._update_bar(self.fm_bar, "Front Mid", features[1])
                self._update_bar(self.fr_bar, "Front Right", features[2])
                self._update_bar(self.ml_bar, "Mid Left", features[3])
                self._update_bar(self.mm_bar, "Mid Mid", features[4])
                self._update_bar(self.mr_bar, "Mid Right", features[5])
                self._update_bar(self.bl_bar, "Back Left", features[6])
                self._update_bar(self.bm_bar, "Back Mid", features[7])
                self._update_bar(self.br_bar, "Back Right", features[8])

        except Exception as e:
            pass

    def _update_bar(self, bar_dict, name, value):
        val_pct = int(value * 100)
        bar_dict["lbl"].configure(text=f"{name}: {val_pct}%")
        bar_dict["progress"].set(value)
