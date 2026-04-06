"""Tests for the sensor data preprocessor."""

import numpy as np
import pytest
from data.schema import SensorReading
from ai.preprocessor import Preprocessor, _FSR_MAX


def make_reading(tl=2000, tr=2000, bl=2000, br=2000, temp=36.5) -> SensorReading:
    return SensorReading(
        fsr_top_left=tl, fsr_top_right=tr,
        fsr_bottom_left=bl, fsr_bottom_right=br,
        temperature=temp,
    )


class TestPersonDetection:
    def setup_method(self):
        self.proc = Preprocessor(temperature_threshold=30.0)

    def test_person_present_above_threshold(self):
        reading = make_reading(temp=36.5)
        assert self.proc.is_person_present(reading) is True

    def test_no_person_below_threshold(self):
        reading = make_reading(temp=24.0)
        assert self.proc.is_person_present(reading) is False

    def test_exactly_at_threshold(self):
        reading = make_reading(temp=30.0)
        assert self.proc.is_person_present(reading) is True

    def test_just_below_threshold(self):
        reading = make_reading(temp=29.99)
        assert self.proc.is_person_present(reading) is False


class TestFeatureExtraction:
    def setup_method(self):
        self.proc = Preprocessor()

    def test_output_shape(self):
        reading = make_reading()
        features = self.proc.extract_features(reading)
        assert features.shape == (4,)

    def test_output_dtype_float32(self):
        features = self.proc.extract_features(make_reading())
        assert features.dtype == np.float32

    def test_values_in_unit_range(self):
        reading = make_reading(tl=0, tr=4095, bl=2048, br=1024)
        features = self.proc.extract_features(reading)
        assert features.min() >= 0.0
        assert features.max() <= 1.0

    def test_zero_input_gives_zero(self):
        reading = make_reading(tl=0, tr=0, bl=0, br=0)
        features = self.proc.extract_features(reading)
        np.testing.assert_array_equal(features, np.zeros(4, dtype=np.float32))

    def test_full_scale_normalises_to_one(self):
        reading = make_reading(tl=4095, tr=4095, bl=4095, br=4095)
        features = self.proc.extract_features(reading)
        np.testing.assert_allclose(features, np.ones(4, dtype=np.float32), atol=1e-5)

    def test_order_tl_tr_bl_br(self):
        """Verify the feature vector order matches [TL, TR, BL, BR]."""
        reading = make_reading(tl=4095, tr=0, bl=0, br=0)
        features = self.proc.extract_features(reading)
        assert features[0] == pytest.approx(1.0, abs=1e-4)
        assert features[1] == pytest.approx(0.0, abs=1e-4)
        assert features[2] == pytest.approx(0.0, abs=1e-4)
        assert features[3] == pytest.approx(0.0, abs=1e-4)

    def test_custom_threshold_propagated(self):
        proc = Preprocessor(temperature_threshold=35.0)
        assert proc._temp_threshold == 35.0
