"""Tests for the AI InferenceEngine (rule-based stub path)."""

import numpy as np
import pytest
from ai.inference_engine import InferenceEngine
from data.schema import PostureLabel


@pytest.fixture
def engine(tmp_path):
    """Engine instance using the rule-based stub (no model file)."""
    # tmp_path / "no_model.pt" does not exist → forces stub mode
    return InferenceEngine(model_path=str(tmp_path / "no_model.pt"))


class TestInferenceEngineStub:
    def test_uses_stub_when_no_model_file(self, engine):
        assert engine.using_stub is True

    def test_predict_returns_tuple(self, engine):
        features = np.array([0.5]*9, dtype=np.float32)
        result = engine.predict(features)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_predicted_label_is_posture_label(self, engine):
        features = np.array([0.5]*9, dtype=np.float32)
        label, conf = engine.predict(features)
        assert isinstance(label, PostureLabel)

    def test_confidence_in_unit_range(self, engine):
        features = np.array([0.8, 0.8, 0.8, 0.1, 0.1, 0.1, 0.8, 0.8, 0.8], dtype=np.float32)
        _, conf = engine.predict(features)
        assert 0.0 <= conf <= 1.0


class TestRuleBasedClassifier:
    """Test the heuristic rule-based classifier directly."""

    def _predict(self, fl, fm, fr, ml, mm, mr, bl, bm, br):
        features = np.array([fl, fm, fr, ml, mm, mr, bl, bm, br], dtype=np.float32)
        return InferenceEngine._rule_based_predict(features)

    def test_balanced_is_correct(self):
        label, _ = self._predict(0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
        assert label == PostureLabel.CORRECT

    def test_lean_left_detected(self):
        # Much more weight on left (TL + BL) than right (TR + BR)
        label, conf = self._predict(0.9, 0.1, 0.1, 0.9, 0.1, 0.1, 0.9, 0.1, 0.1)
        assert label == PostureLabel.LEAN_LEFT
        assert conf > 0.5

    def test_lean_right_detected(self):
        label, conf = self._predict(0.1, 0.1, 0.9, 0.1, 0.1, 0.9, 0.1, 0.1, 0.9)
        assert label == PostureLabel.LEAN_RIGHT
        assert conf > 0.5

    def test_slouch_forward_detected(self):
        # More weight on front (TL + TR) than back (BL + BR)
        label, conf = self._predict(0.9, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1)
        assert label == PostureLabel.SLOUCH_FORWARD
        assert conf > 0.5

    def test_lean_back_detected(self):
        label, conf = self._predict(0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.9, 0.9, 0.9)
        assert label == PostureLabel.LEAN_BACK
        assert conf > 0.5

    def test_near_zero_total_returns_correct(self):
        label, _ = self._predict(0.0, 0.0, 0.0, 0.01, 0.0, 0.005, 0.0, 0.0, 0.0)
        # Below the negligible-total guard → CORRECT
        assert label == PostureLabel.CORRECT
