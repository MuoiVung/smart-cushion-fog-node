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

import logging
from pathlib import Path
from typing import Tuple

import joblib
import numpy as np

from data.schema import PostureLabel

logger = logging.getLogger(__name__)

# ── Full ordered label list (model output index → PostureLabel) ────────────
# Update this when a multi-class model is trained:
# ── 9-posture model (excludes empty and object) ──────────────────────────
POSTURE_LABELS_9: list[PostureLabel] = [
    PostureLabel.NUP,     # 0
    PostureLabel.LF,      # 1
    PostureLabel.LB,      # 2
    PostureLabel.LFSR,    # 3
    PostureLabel.LFSL,    # 4
    PostureLabel.CRL,     # 5
    PostureLabel.CLL,     # 6
    PostureLabel.CRLL,    # 7
    PostureLabel.CLLL,    # 8
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
# score ≥ 0.5 → "Sitting Straight" → NUP
# score < 0.5 → "Incorrect/Leaning" → LF (generic bad posture)
_BINARY_POS_LABEL = PostureLabel.NUP   # score ≥ 0.5
_BINARY_NEG_LABEL = PostureLabel.LF    # score < 0.5

# FSR total-pressure thresholds for the rule-based regime
_EMPTY_THRESHOLD  = 1000    # below this → empty (sum of all 9 ADC values)
_OBJECT_THRESHOLD = 3000    # 1000 – 3000 → might be an object, not a person


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
        model_path:  str = "ai/models/smart_cushion_model.h5",
        scaler_path: str = "ai/models/fsr_scaler.pkl",
    ) -> None:
        self._model_path  = Path(model_path)
        self._scaler_path = Path(scaler_path)
        self._model  = None
        self._scaler = None
        self._use_stub = False
        self._is_binary = True   # True until an 11-class model is available

        self._load_model()

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def using_stub(self) -> bool:
        """True when no trained model was found and the rule-based stub is active."""
        return self._use_stub

    def predict(self, raw_sensors: np.ndarray) -> Tuple[PostureLabel, float]:
        """
        Predict an 11-label posture/state from raw FSR sensor values.

        Args:
            raw_sensors: numpy int/float array of shape (9,) with raw ADC values
                         [FL, FM, FR, ML, MM, MR, BL, BM, BR], range 0–4095.
                         NOTE: raw values (not normalised) — scaler handles this.

        Returns:
            Tuple of (PostureLabel, confidence) where confidence ∈ [0.0, 1.0].
        """
        # ── Step 1: Empty Detection (Heuristic) ─────────────────────────────
        # If total pressure is very low, it's definitely empty.
        total_pressure = float(np.sum(raw_sensors))
        if total_pressure < _EMPTY_THRESHOLD:
            return PostureLabel.EMPTY, 1.0

        # ── Step 2: Run AI Inference ────────────────────────────────────────
        if self._use_stub:
            return self._rule_based_predict(raw_sensors)

        return self._keras_predict(raw_sensors)

    # ── Private: Keras CNN inference ───────────────────────────────────────

    def _keras_predict(self, raw_sensors: np.ndarray) -> Tuple[PostureLabel, float]:
        """
        Run the Keras CNN model (currently binary, maps to 11-class output).

        Input pipeline:
          raw ADC (9,) → MinMaxScaler → reshape(1, 3, 3, 1) → CNN → sigmoid score
        """
        try:
            import pandas as pd

            fsr_cols = [
                'FSR Front Left',  'FSR Front Mid',  'FSR Front Right',
                'FSR Mid Left',    'FSR Mid Mid',    'FSR Mid Right',
                'FSR Back Left',   'FSR Back Mid',   'FSR Back Right',
            ]
            input_df = pd.DataFrame([raw_sensors.tolist()], columns=fsr_cols)
            scaled   = self._scaler.transform(input_df)     # (1, 9) MinMax scaled
            cnn_in   = scaled.reshape(1, 3, 3, 1)           # reshape for Conv2D

            # Get full prediction array
            predictions = self._model.predict(cnn_in, verbose=0)[0]

            if self._is_binary:
                # Binary model: score ≥ 0.5 → NUP (correct), < 0.5 → LF (bad)
                score = float(predictions[0])
                if score >= 0.5:
                    label      = _BINARY_POS_LABEL
                    confidence = score
                else:
                    label      = _BINARY_NEG_LABEL
                    confidence = 1.0 - score
                logger.debug(f"Keras binary prediction: {label.value} (score={score:.4f})")
            else:
                # Multi-class: find index with highest probability
                predicted_idx = int(np.argmax(predictions))
                confidence    = float(predictions[predicted_idx])
                
                # Use appropriate label list based on output count
                if len(predictions) == 9:
                    label = POSTURE_LABELS_9[predicted_idx]
                else:
                    label = POSTURE_LABELS_11[predicted_idx]
                
                logger.debug(f"Keras multi-class prediction: {label.value} (idx={predicted_idx}, conf={confidence:.3f})")

            return label, round(confidence, 4)

        except Exception as exc:
            logger.error(f"Keras inference failed: {exc}. Falling back to rule-based.")
            return self._rule_based_predict(raw_sensors)

    # ── Private: model loading ─────────────────────────────────────────────

    def _load_model(self) -> None:
        """Load Keras model and sklearn scaler from disk."""
        model_ok  = self._model_path.exists()
        scaler_ok = self._scaler_path.exists()

        if not model_ok or not scaler_ok:
            missing = []
            if not model_ok:  missing.append(str(self._model_path))
            if not scaler_ok: missing.append(str(self._scaler_path))
            logger.warning(
                f"AI model file(s) not found: {missing}. "
                "Falling back to rule-based classifier. "
                "Copy smart_cushion_model.h5 and fsr_scaler.pkl to ai/models/."
            )
            self._use_stub = True
            return

        try:
            import tensorflow as tf
            self._model  = tf.keras.models.load_model(str(self._model_path), compile=False)
            self._scaler = joblib.load(str(self._scaler_path))

            # Detect binary vs multi-class from output shape
            output_units = self._model.output_shape[-1]
            if output_units == 1:
                self._is_binary = True
                logger.info(
                    f"Keras binary CNN loaded from '{self._model_path}' "
                    f"(maps to NUP / LF for now — retrain with 11 classes for full support)"
                )
            else:
                self._is_binary = False
                logger.info(
                    f"Keras {output_units}-class CNN loaded from '{self._model_path}'"
                )
        except Exception as exc:
            logger.error(
                f"Failed to load Keras model: {exc}. Falling back to rule-based classifier."
            )
            self._model  = None
            self._scaler = None
            self._use_stub = True

    # ── Private: rule-based fallback ───────────────────────────────────────

    @staticmethod
    def _rule_based_predict(raw_sensors: np.ndarray) -> Tuple[PostureLabel, float]:
        """
        Heuristic posture classifier using FSR pressure symmetry.

        Uses raw ADC values directly (normalisation done internally).
        Returns one of: NUP, LF, LB, LFSR, LFSL  (simplified 5-label subset).
        CRLL / CLLL / CRL / CLL require training data to distinguish reliably.
        """
        fl, fm, fr, ml, mm, mr, bl, bm, br = raw_sensors.tolist()
        total = fl + fm + fr + ml + mm + mr + bl + bm + br

        if total < 1:
            return PostureLabel.NUP, 0.50

        # Relative proportion per sensor
        left  = (fl + ml + bl) / total
        right = (fr + mr + br) / total
        front = (fl + fm + fr) / total
        back  = (bl + bm + br) / total

        lr_diff = left  - right
        fb_diff = front - back

        THRESHOLD = 0.15

        abs_lr = abs(lr_diff)
        abs_fb = abs(fb_diff)

        if abs_lr >= abs_fb and abs_lr > THRESHOLD:
            if lr_diff > 0:
                label = PostureLabel.LFSL   # more pressure left → lean fwd-support left
            else:
                label = PostureLabel.LFSR   # more pressure right
            confidence = min(0.90, 0.45 + abs_lr)
        elif abs_fb > abs_lr and abs_fb > THRESHOLD:
            if fb_diff > 0:
                label = PostureLabel.LF     # more pressure front → lean forward
            else:
                label = PostureLabel.LB     # more pressure back → lean backward
            confidence = min(0.90, 0.45 + abs_fb)
        else:
            label      = PostureLabel.NUP
            confidence = max(0.40, 0.85 - max(abs_lr, abs_fb))

        logger.debug(f"Rule-based prediction: {label.value} (confidence={confidence:.3f})")
        return label, round(confidence, 4)
