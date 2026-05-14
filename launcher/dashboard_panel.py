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
            self, text="Đang chờ dữ liệu...", 
            font=ctk.CTkFont(size=24, weight="bold")
        )
        self.posture_label.grid(row=0, column=0, columnspan=3, pady=(20, 10))
        
        self.confidence_label = ctk.CTkLabel(
            self, text="Độ tin cậy: --%", 
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
            features_frame, text="Đặc trưng AI (Tỷ lệ áp lực %)",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="gray"
        ).grid(row=0, column=0, columnspan=3, pady=(10, 10))

        self.fl_bar = self._create_feature_bar(features_frame, "Trước Trái", 1, 0)
        self.fm_bar = self._create_feature_bar(features_frame, "Trước Giữa", 1, 1)
        self.fr_bar = self._create_feature_bar(features_frame, "Trước Phải", 1, 2)
        
        self.ml_bar = self._create_feature_bar(features_frame, "Giữa Trái", 2, 0)
        self.mm_bar = self._create_feature_bar(features_frame, "Giữa Giữa", 2, 1)
        self.mr_bar = self._create_feature_bar(features_frame, "Giữa Phải", 2, 2)
        
        self.bl_bar = self._create_feature_bar(features_frame, "Sau Trái", 3, 0)
        self.bm_bar = self._create_feature_bar(features_frame, "Sau Giữa", 3, 1)
        self.br_bar = self._create_feature_bar(features_frame, "Sau Phải", 3, 2)

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
            occupancy = payload_dict.get("occupancy_state", "empty").lower()
            if occupancy == "empty":
                self.posture_label.configure(text="Không phát hiện người ngồi", text_color="gray")
                self.confidence_label.configure(text="Thời lượng: 0s")
                self._update_bar(self.fl_bar, "Trước Trái", 0.0)
                self._update_bar(self.fm_bar, "Trước Giữa", 0.0)
                self._update_bar(self.fr_bar, "Trước Phải", 0.0)
                self._update_bar(self.ml_bar, "Giữa Trái", 0.0)
                self._update_bar(self.mm_bar, "Giữa Giữa", 0.0)
                self._update_bar(self.mr_bar, "Giữa Phải", 0.0)
                self._update_bar(self.bl_bar, "Sau Trái", 0.0)
                self._update_bar(self.bm_bar, "Sau Giữa", 0.0)
                self._update_bar(self.br_bar, "Sau Phải", 0.0)
                return

            posture = payload_dict.get("posture", "EMPTY").upper()
            if posture == "EMPTY":
                color = "gray"
            elif posture == "OBJECT":
                color = "#d29922" # warning
            # Color logic
            if posture == "CORRECT":
                color = "#3fb950"  # Green
            elif posture == "EMPTY":
                color = "gray"
            else:
                color = "#f85149"  # Red
                
            self.posture_label.configure(text=f"Tư thế: {posture}", text_color=color)
            
            duration = payload_dict.get("session_duration_sec", 0)
            self.confidence_label.configure(text=f"Thời lượng: {duration}s")

            features = payload_dict.get("sensors_heatmap_pct", [])
            if features and len(features) == 9:
                self._update_bar(self.fl_bar, "Trước Trái", features[0])
                self._update_bar(self.fm_bar, "Trước Giữa", features[1])
                self._update_bar(self.fr_bar, "Trước Phải", features[2])
                self._update_bar(self.ml_bar, "Giữa Trái", features[3])
                self._update_bar(self.mm_bar, "Giữa Giữa", features[4])
                self._update_bar(self.mr_bar, "Giữa Phải", features[5])
                self._update_bar(self.bl_bar, "Sau Trái", features[6])
                self._update_bar(self.bm_bar, "Sau Giữa", features[7])
                self._update_bar(self.br_bar, "Sau Phải", features[8])

        except Exception as e:
            pass

    def _update_bar(self, bar_dict, name, value):
        val_pct = int(value)
        bar_dict["lbl"].configure(text=f"{name}: {val_pct}%")
        bar_dict["progress"].set(value / 100.0)
