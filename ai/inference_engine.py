"""
AI Inference Engine for the Smart Cushion Fog Node.

Integrates the trained Keras CNN model from smart-cushion-AI:
  - Model  : ai/models/smart_cushion_model.h5  (Keras CNN 2D, binary → extensible)
  - Scaler : ai/models/fsr_scaler.pkl          (sklearn MinMaxScaler)

11-label architecture (system_architecture.md §1):
  ┌─────────────────────────────────────────────────────────────────────┐
  │ Label index │ PostureLabel │ Meaning                               │
  │─────────────│──────────────│───────────────────────────────────────│
  │      0      │  empty       │ Cushion is empty                      │
  │      1      │  object      │ Non-human object on cushion           │
  │      2      │  NUP         │ Natural Upright Posture (correct)     │
  │      3      │  LF          │ Lean Forward                          │
  │      4      │  LB          │ Lean Backward                         │
  │      5      │  LFSR        │ Lean Fwd – Support Right              │
  │      6      │  LFSL        │ Lean Fwd – Support Left               │
  │      7      │  CRL         │ Cross-Right Legged                    │
  │      8      │  CLL         │ Cross-Left Legged                     │
  │      9      │  CRLL        │ Cross-Right Legged-Legged             │
  │     10      │  CLLL        │ Cross-Left Legged-Legged              │
  │     11      │  UNKNOWN     │ Unknown or error state                │
  └─────────────────────────────────────────────────────────────────────┘

Current trained model (smart_cushion_model.h5):
  - Binary CNN: 0 = Incorrect/Leaning, 1 = Sitting Straight
  - The engine wraps this as a 2-label subset (maps: 1→NUP, 0→LF)
    and uses rule-based heuristics for the remaining labels
    (empty, object, LB, LFSR, LFSL, CRL, CLL, CRLL, CLLL).
  - When an 11-class model is trained, replace the .h5 file and
    update NUM_CLASSES + POSTURE_LABELS below — no other changes needed.

Fallback:
  If the model file or scaler cannot be loaded, a rule-based heuristic
  classifier maintains full functionality during development.
"""

import os
import json
import logging
import joblib
import numpy as np
from pathlib import Path
from typing import Tuple, List, Dict, Optional

from data.schema import PostureLabel

logger = logging.getLogger(__name__)

# ── Full ordered label list (model output index → PostureLabel) ────────────
# Update this when a multi-class model is trained:
POSTURE_LABELS_5: list[PostureLabel] = [
    PostureLabel.UPRIGHT,  # 0
    PostureLabel.FORWARD,  # 1
    PostureLabel.BACKWARD, # 2
    PostureLabel.RIGHT,    # 3
    PostureLabel.LEFT,     # 4
]

# Full 11-label list (for legacy/future support)
POSTURE_LABELS_11: list[PostureLabel] = [
    PostureLabel.EMPTY,
    PostureLabel.OBJECT,
    PostureLabel.NUP,
    PostureLabel.LF,
    PostureLabel.LB,
    PostureLabel.LFSR,
    PostureLabel.LFSL,
    PostureLabel.CRL,
    PostureLabel.CLL,
    PostureLabel.CRLL,
    PostureLabel.CLLL,
]

# ── Current binary model output mapping ───────────────────────────────────
# score ≥ 0.5 → "Sitting Upright" → UPRIGHT
# score < 0.5 → "Incorrect/Leaning" → FORWARD (generic bad posture)
_BINARY_POS_LABEL = PostureLabel.UPRIGHT   # score ≥ 0.5
_BINARY_NEG_LABEL = PostureLabel.FORWARD   # score < 0.5

# FSR total-pressure thresholds for the rule-based regime
_EMPTY_THRESHOLD  = 1000    # below this → empty (sum of all 9 ADC values)
_OBJECT_THRESHOLD = 3000    # 1000 – 3000 → might be an object, not a person


def _extract_22_features(raw_2d: np.ndarray) -> np.ndarray:
    """
    Convert (1, 9) raw FSR array into (1, 22) feature vector.
    Identical to extract_features() in train_rf.py / train_fnn.py.
    """
    f = raw_2d.astype(float)
    total = f.sum(axis=1, keepdims=True)
    ts = np.where(total == 0, 1.0, total) # shape (N, 1)

    # 1. Normalize raw FSR inputs (sum-to-1 per row)
    f_norm = f / ts

    front = f_norm[:, [0, 1, 2]].sum(1)
    back  = f_norm[:, [6, 7, 8]].sum(1)
    left  = f_norm[:, [0, 3, 6]].sum(1)
    right = f_norm[:, [2, 5, 8]].sum(1)
    mid_r = f_norm[:, [3, 4, 5]].sum(1)

    # Center of Pressure (–1 … +1)
    cop_x = right - left
    cop_y = front - back

    # Diagonal asymmetry
    diag_main = f_norm[:, 0] + f_norm[:, 4] + f_norm[:, 8]
    diag_anti = f_norm[:, 2] + f_norm[:, 4] + f_norm[:, 6]
    diag_diff = diag_main - diag_anti

    # Weight-invariant statistics
    std_v = f_norm.std(1)
    max_v = f_norm.max(1)
    min_v = f_norm.min(1)
    var_v = f_norm.var(1)

    # 13 engineered features (all normalized)
    engineered = np.stack([
        cop_x, cop_y, diag_diff,
        std_v, max_v, min_v, var_v,
        front, back,
        left, right,
        mid_r, f_norm[:, 4],
    ], axis=1) # shape (N, 13)

    return np.concatenate([f_norm, engineered], axis=1) # (N, 22)


class InferenceEngine:
    """
    Manages model lifecycle (load, eval, predict) with a graceful fallback.

    Load priority:
      1. Keras CNN model (.h5) + sklearn scaler (.pkl) from smart-cushion-AI
      2. Rule-based heuristic classifier (always available as fallback)

    Usage:
        engine = InferenceEngine(
            model_path  = "ai/models/smart_cushion_model.h5",
            scaler_path = "ai/models/fsr_scaler.pkl",
        )
        label, confidence = engine.predict(raw_sensor_array)
    """

    def __init__(
        self,
        model_type:  str = "keras",
        model_path:  str = "ai/models/smart_cushion_model.h5",
        scaler_path: str = "ai/models/fsr_scaler.pkl",
    ) -> None:
        self._model_type  = model_type
        self._model_path  = Path(model_path)
        self._scaler_path = Path(scaler_path) if scaler_path else None
        self._model = None
        self._scaler = None
        self._le = None # Label Encoder for RF
        self._is_binary = True   # True until an 11-class model is available

        self._load_model()

    # ── Public API ─────────────────────────────────────────────────────────

    def reload(self, model_type: str, model_path: str, scaler_path: str) -> bool:
        """
        Hot-reload model and scaler in-place without restarting the Fog Node.

        If loading the new files fails, the previous model is automatically
        restored so inference continues uninterrupted.

        Returns:
            True if reload succeeded, False if it failed (old model kept active).
        """
        old_model_type = self._model_type
        old_model      = self._model
        old_scaler     = self._scaler
        old_is_binary  = self._is_binary
        old_model_path = self._model_path
        old_scaler_path= self._scaler_path

        self._model_type  = model_type
        self._model_path  = Path(model_path)
        self._scaler_path = Path(scaler_path) if scaler_path else None
        self._model       = None
        self._scaler      = None

        self._load_model()

        if self._model is None and old_model is not None:
            # New model failed to load — restore previous state
            self._model_type  = old_model_type
            self._model       = old_model
            self._scaler      = old_scaler
            self._is_binary   = old_is_binary
            self._model_path  = old_model_path
            self._scaler_path = old_scaler_path
            logger.error("[HOT-RELOAD] ❌ Failed to load new model — previous model is still active")
            return False

        sp_name = self._scaler_path.name if self._scaler_path else "none"
        logger.info(f"[HOT-RELOAD] ✅ Model swapped: [{self._model_type}] {self._model_path.name} + {sp_name}")
        return True

    def predict(self, raw_sensors: np.ndarray) -> Tuple[PostureLabel, float, list[dict]]:
        """
        Predict an 11-label posture/state from raw FSR sensor values.

        Args:
            raw_sensors: numpy int/float array of shape (9,) with raw ADC values
                         [FL, FM, FR, ML, MM, MR, BL, BM, BR], range 0–4095.
                         NOTE: raw values (not normalised) — scaler handles this.

        Returns:
            Tuple of (PostureLabel, confidence, top_3)
            - confidence: float ∈ [0.0, 1.0].
            - top_3: list of {"label": str, "confidence": float} sorted by confidence desc.
        """
        # ── Step 1: Empty Detection (Heuristic) ─────────────────────────────
        # If total pressure is very low, it's definitely empty.
        total_pressure = float(np.sum(raw_sensors))
        if total_pressure < 200:
            return PostureLabel.EMPTY, 1.0, [{"label": "empty", "confidence": 1.0}]

        # ── Step 2: Run AI Inference ────────────────────────────────────────
        return self._ai_predict(raw_sensors)

    # ── Private: Model inference ───────────────────────────────────────

    def _ai_predict(self, raw_sensors: np.ndarray) -> Tuple[PostureLabel, float, list[dict]]:
        """
        Run the selected AI model.
        """
        try:
            if self._model is None:
                raise ValueError("Model not loaded.")
                
            if self._model_type in ("keras", "tiny_cnn", "resnet"):
                # Tiny CNN / Micro ResNet: 9 raw → L1 normalise → reshape (1,3,3,1)
                if self._scaler is None:
                    raise ValueError("Scaler not loaded for Keras CNN/ResNet model.")
                raw_2d   = np.array(raw_sensors, dtype=float).reshape(1, 9)
                scaled   = self._scaler.transform(raw_2d)    # L1 Normalizer
                cnn_in   = scaled.reshape(1, 3, 3, 1)
                predictions = self._model.predict(cnn_in, verbose=0)[0]

            elif self._model_type == "fnn":
                # Hybrid FNN: 22 features (9 raw + 13 physics) → StandardScaler
                if self._scaler is None:
                    raise ValueError("Scaler not loaded for FNN model.")
                raw_2d   = np.array(raw_sensors, dtype=float).reshape(1, 9)
                feats    = _extract_22_features(raw_2d)       # (1, 22)
                scaled   = self._scaler.transform(feats)
                predictions = self._model.predict(scaled, verbose=0)[0]

            elif self._model_type == "random_forest":
                # Random Forest: 22 features, no scaler needed
                raw_2d      = np.array(raw_sensors, dtype=float).reshape(1, 9)
                input_final = _extract_22_features(raw_2d)   # (1, 22)
                predictions = self._model.predict_proba(input_final)[0]

            else:
                raise ValueError(f"Unknown model type: {self._model_type}")

            if self._model_type == "keras" and self._is_binary:
                # Binary model: score ≥ 0.5 → NUP (correct), < 0.5 → LF (bad)
                score = float(predictions[0])
                if score >= 0.5:
                    label      = _BINARY_POS_LABEL
                    confidence = score
                else:
                    label      = _BINARY_NEG_LABEL
                    confidence = 1.0 - score
                logger.debug(f"Keras binary prediction: {label.value} (score={score:.4f})")
                top_3 = [{"label": label.value, "confidence": round(confidence, 4)}]
            else:
                # Multi-class: find index with highest probability
                predicted_idx = int(np.argmax(predictions))
                confidence    = float(predictions[predicted_idx])
                
                # Use appropriate label list based on output count
                # If exactly 11 classes, use 11-label list (includes Empty/Object)
                # Otherwise assume it's a 5-posture model
                labels = POSTURE_LABELS_11 if len(predictions) == 11 else POSTURE_LABELS_5
                label = labels[predicted_idx]

                # Get top 3
                top_indices = np.argsort(predictions)[-3:][::-1]
                top_3 = []
                for idx in top_indices:
                    top_3.append({
                        "label": labels[int(idx)].value,
                        "confidence": float(round(predictions[idx], 4))
                    })
                
                logger.debug(f"AI multi-class prediction: {label.value} (idx={predicted_idx}, conf={confidence:.3f})")

            return label, round(confidence, 4), top_3

        except Exception as exc:
            if self._model is None:
                error_text = "❌ AI INFERENCE ERROR: Model not loaded."
            else:
                error_text = f"❌ AI INFERENCE CRITICAL ERROR: {exc}"
            
            import traceback
            err_msg = traceback.format_exc()
            with open("predict_error.log", "a") as f:
                f.write(err_msg + "\n")
            
            logger.error(error_text)
            print(error_text)  # Ensure it prints to stdout for the launcher console log
            return PostureLabel.UNKNOWN, 0.0, []

    # ── Private: model loading ─────────────────────────────────────────────

    def _load_model(self) -> None:
        """Load model and scaler from disk."""
        model_ok  = self._model_path.exists()
        scaler_ok = True
        
        if self._model_type in ("keras", "tiny_cnn", "resnet", "fnn"):
            scaler_ok = self._scaler_path and self._scaler_path.exists()

        if not model_ok or not scaler_ok:
            missing = []
            if not model_ok:  missing.append(str(self._model_path.absolute()))
            if not scaler_ok: missing.append(str(self._scaler_path.absolute()) if self._scaler_path else "None")
            error_text = (
                f"❌ CRITICAL: AI MODEL FILES MISSING! Checked: {missing}. "
                "System will NOT provide accurate predictions."
            )
            logger.error(error_text)
            print(error_text)
            return

        try:
            if self._model_type in ("keras", "tiny_cnn", "resnet", "fnn"):
                import tensorflow as tf
                self._model  = tf.keras.models.load_model(str(self._model_path), compile=False)
                self._scaler = joblib.load(str(self._scaler_path))

                # Detect binary vs multi-class from output shape
                output_units = self._model.output_shape[-1]
                if output_units == 1:
                    self._is_binary = True
                    logger.info(
                        f"Keras binary model loaded from '{self._model_path}'"
                    )
                else:
                    self._is_binary = False
                    logger.info(
                        f"Keras {output_units}-class model ({self._model_type}) loaded from '{self._model_path}'"
                    )
            elif self._model_type == "random_forest":
                # Nạp mô hình Pickle
                self._model = joblib.load(str(self._model_path))
                self._scaler = None
                self._is_binary = False
                
                # Tự động tìm và nạp LabelEncoder (tên_file_le.pkl)
                self._le = None
                le_path = str(self._model_path).replace(".pkl", "_le.pkl")
                if os.path.exists(le_path):
                    self._le = joblib.load(le_path)
                    logger.info(f"LabelEncoder loaded from '{le_path}'")
                
                logger.info(f"{self._model_type.replace('_', ' ').title()} model loaded from '{self._model_path}'")
                
        except Exception as exc:
            import traceback
            err_msg = traceback.format_exc()
            with open("model_load_error.log", "w") as f:
                f.write(err_msg)
            error_text = f"❌ FAILED TO LOAD AI MODEL: {exc}. Check model_load_error.log"
            logger.error(error_text)
            print(error_text)
            self._model  = None
            self._scaler = None


    # ── Private: rule-based fallback ───────────────────────────────────────

    # @staticmethod
    # def _rule_based_predict(raw_sensors: np.ndarray) -> Tuple[PostureLabel, float, list[dict]]:
    #     """
    #     Heuristic posture classifier using FSR pressure symmetry.
    # 
    #     Uses raw ADC values directly (normalisation done internally).
    #     Returns one of: NUP, LF, LB, LFSR, LFSL  (simplified 5-label subset).
    #     CRLL / CLLL / CRL / CLL require training data to distinguish reliably.
    #     """
    #     fl, fm, fr, ml, mm, mr, bl, bm, br = raw_sensors.tolist()
    #     total = fl + fm + fr + ml + mm + mr + bl + bm + br
    # 
    #     if total < 1:
    #         return PostureLabel.NUP, 0.50
    # 
    #     # Relative proportion per sensor
    #     left  = (fl + ml + bl) / total
    #     right = (fr + mr + br) / total
    #     front = (fl + fm + fr) / total
    #     back  = (bl + bm + br) / total
    # 
    #     lr_diff = left  - right
    #     fb_diff = front - back
    # 
    #     THRESHOLD = 0.15
    # 
    #     abs_lr = abs(lr_diff)
    #     abs_fb = abs(fb_diff)
    # 
    #     if abs_lr >= abs_fb and abs_lr > THRESHOLD:
    #         if lr_diff > 0:
    #             label = PostureLabel.LFSL   # more pressure left → lean fwd-support left
    #         else:
    #             label = PostureLabel.LFSR   # more pressure right
    #         confidence = min(0.90, 0.45 + abs_lr)
    #     elif abs_fb > abs_lr and abs_fb > THRESHOLD:
    #         if fb_diff > 0:
    #             label = PostureLabel.LF     # more pressure front → lean forward
    #         else:
    #             label = PostureLabel.LB     # more pressure back → lean backward
    #         confidence = min(0.90, 0.45 + abs_fb)
    #     else:
    #         label      = PostureLabel.NUP
    #         confidence = max(0.40, 0.85 - max(abs_lr, abs_fb))
    # 
    #     logger.debug(f"Rule-based prediction: {label.value} (confidence={confidence:.3f})")
    #     
    #     # Simulated top-3 for heuristic
    #     top_3 = [{"label": label.value, "confidence": round(confidence, 4)}]
    #     # Add a placeholder for others to show it's heuristic
    #     top_3.append({"label": "[Heuristic]", "confidence": 0.0})
    #     
    #     return label, round(confidence, 4), top_3

