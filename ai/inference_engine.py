"""
AI Inference Engine for the Smart Cushion Fog Node.

This module provides:
  PostureMLP  – Lightweight PyTorch MLP model architecture.
  InferenceEngine – High-level wrapper that loads weights and runs prediction.

If no trained model file is found, InferenceEngine automatically falls back
to a built-in rule-based heuristic classifier so the system remains
fully functional during development and before a model is trained.

Model architecture (PostureMLP):
  Input  layer : 9 neurons  (normalised FSR values, 3x3 matrix)
  Hidden layer : 32 → 16 neurons (ReLU + BatchNorm + Dropout)
  Output layer : 5 neurons  (posture class logits)

Posture class indices:
  0 = correct
  1 = lean_left
  2 = lean_right
  3 = slouch_forward
  4 = lean_back
"""

import logging
from pathlib import Path
from typing import Tuple

import numpy as np
import torch
import torch.nn as nn

from data.schema import PostureLabel

logger = logging.getLogger(__name__)

# Ordered list mapping model output index -> PostureLabel
POSTURE_LABELS: list[PostureLabel] = [
    PostureLabel.CORRECT,
    PostureLabel.LEAN_LEFT,
    PostureLabel.LEAN_RIGHT,
    PostureLabel.SLOUCH_FORWARD,
    PostureLabel.LEAN_BACK,
]


# ---------------------------------------------------------------------------
# Model Architecture
# ---------------------------------------------------------------------------

class PostureMLP(nn.Module):
    """
    Multi-Layer Perceptron for posture classification.

    This is the canonical model architecture. Train this class and
    save its state dict with:
        torch.save(model.state_dict(), "ai/models/posture_model.pt")

    Architecture:
        Linear(4, 32) -> BatchNorm1d(32) -> ReLU -> Dropout(0.2)
        -> Linear(32, 16) -> ReLU
        -> Linear(16, 5)   [logits, no softmax – use CrossEntropyLoss]
    """

    NUM_CLASSES = 5
    INPUT_DIM   = 9

    def __init__(self) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(self.INPUT_DIM, 32),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.Dropout(p=0.2),
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, self.NUM_CLASSES),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: Float tensor of shape (batch_size, 9).
        Returns:
            Logit tensor of shape (batch_size, 5).
        """
        return self.network(x)


# ---------------------------------------------------------------------------
# Inference Engine
# ---------------------------------------------------------------------------

class InferenceEngine:
    """
    Manages model lifecycle (load, eval, predict) with a graceful fallback.

    Usage:
        engine = InferenceEngine(model_path="ai/models/posture_model.pt")
        label, confidence = engine.predict(feature_vector)
    """

    def __init__(self, model_path: str) -> None:
        """
        Args:
            model_path: Path to a saved PostureMLP state dict (.pt file).
                        If the file does not exist, the rule-based stub is used.
        """
        self._model_path = Path(model_path)
        self._model: PostureMLP | None = None
        self._use_stub = False
        self._load_model()

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def using_stub(self) -> bool:
        """True when no trained model was found and the rule-based stub is active."""
        return self._use_stub

    def predict(self, features: np.ndarray) -> Tuple[PostureLabel, float]:
        """
        Predict posture from a normalised 9-element feature vector.

        Args:
            features: numpy float32 array of shape (9,) with values in [0, 1].
                      Order: [fl, fm, fr, ml, mc, mr, bl, bm, br]

        Returns:
            Tuple of (PostureLabel, confidence) where confidence ∈ [0.0, 1.0].
        """
        if self._use_stub:
            return self._rule_based_predict(features)

        # ── PyTorch inference ────────────────────────────────────────────
        with torch.no_grad():
            x = torch.from_numpy(features).float().unsqueeze(0)  # shape: (1, 9)
            logits = self._model(x)                              # shape: (1, 5)
            probs = torch.softmax(logits, dim=-1)                # shape: (1, 5)
            predicted_idx = int(probs.argmax(dim=-1).item())
            confidence = float(probs[0, predicted_idx].item())

        label = POSTURE_LABELS[predicted_idx]
        logger.debug(f"PyTorch prediction: {label.value} (confidence={confidence:.3f})")
        return label, confidence

    # ── Private helpers ────────────────────────────────────────────────────

    def _load_model(self) -> None:
        """Load the PyTorch model state dict from disk."""
        if not self._model_path.exists():
            logger.warning(
                f"Model file not found at '{self._model_path}'. "
                "Falling back to the built-in rule-based classifier. "
                "Train a model and place the .pt file there to enable AI inference."
            )
            self._use_stub = True
            return

        try:
            self._model = PostureMLP()
            # map_location="cpu" ensures the model loads on CPU-only machines
            state_dict = torch.load(self._model_path, map_location="cpu", weights_only=True)
            self._model.load_state_dict(state_dict)
            self._model.eval()
            logger.info(f"PyTorch model loaded from '{self._model_path}'")
        except Exception as exc:
            logger.error(
                f"Failed to load model from '{self._model_path}': {exc}. "
                "Falling back to rule-based classifier."
            )
            self._model = None
            self._use_stub = True

    @staticmethod
    def _rule_based_predict(features: np.ndarray) -> Tuple[PostureLabel, float]:
        """
        Heuristic posture classifier based on FSR pressure symmetry.

        Rationale:
        - Left-leaning: significantly more pressure on left sensors (FL + BL).
        - Right-leaning: significantly more pressure on right sensors (FR + BR).
        - Slouching forward: significantly more pressure on front (FL + FR).
        - Leaning back: significantly more pressure on rear (BL + BR).
        - Otherwise: pressure is relatively symmetric → correct posture.

        The threshold of 0.25 means a side must carry ≥25% more of the total
        weight before a deviation is flagged. Adjust via model training or
        the INCORRECT_POSTURE_ALERT_THRESHOLD setting.

        Args:
            features: [fl, fm, fr, ml, mc, mr, bl, bm, br] normalised in [0, 1].

        Returns:
            (PostureLabel, confidence) with confidence ∈ [0.35, 0.95].
        """
        fl, fm, fr, ml, mc, mr, bl, bm, br = features.tolist()
        total = fl + fm + fr + ml + mc + mr + bl + bm + br

        # Treat negligible total pressure as unknown (should not happen if
        # person_detected is True, but guard against edge cases)
        if total < 0.02:
            return PostureLabel.CORRECT, 0.5

        left  = fl + ml + bl
        right = fr + mr + br
        front = fl + fm + fr
        back  = bl + bm + br

        # Normalised difference: positive → more weight on left/front side
        lr_diff = (left  - right) / (left  + right + 1e-6)
        fb_diff = (front - back)  / (front + back  + 1e-6)

        THRESHOLD = 0.25  # Asymmetry ratio to trigger a deviation label

        abs_lr = abs(lr_diff)
        abs_fb = abs(fb_diff)

        if abs_lr >= abs_fb and abs_lr > THRESHOLD:
            label      = PostureLabel.LEAN_LEFT if lr_diff > 0 else PostureLabel.LEAN_RIGHT
            confidence = min(0.95, 0.35 + abs_lr)
        elif abs_fb > abs_lr and abs_fb > THRESHOLD:
            label      = PostureLabel.SLOUCH_FORWARD if fb_diff > 0 else PostureLabel.LEAN_BACK
            confidence = min(0.95, 0.35 + abs_fb)
        else:
            label      = PostureLabel.CORRECT
            confidence = max(0.35, 0.85 - max(abs_lr, abs_fb))

        logger.debug(f"Rule-based prediction: {label.value} (confidence={confidence:.3f})")
        return label, confidence
