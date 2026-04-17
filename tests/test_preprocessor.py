"""Tests for the sensor data preprocessor."""

import numpy as np
import pytest
from data.schema import SensorReading, AggregatedSensorReading
from ai.preprocessor import Preprocessor, _FSR_MAX


def make_reading(fl=0, fm=2000, fr=0, ml=2000, mm=2000, mr=2000, bl=0, bm=2000, br=0, temp=20.5) -> AggregatedSensorReading:
    """Helper: create an AggregatedSensorReading. Default temp=20.5 matches real ambient hardware."""
    return AggregatedSensorReading(
        fsr_front_left=fl, fsr_front_mid=fm, fsr_front_right=fr,
        fsr_mid_left=ml, fsr_mid_mid=mm, fsr_mid_right=mr,
        fsr_back_left=bl, fsr_back_mid=bm, fsr_back_right=br,
        temperature=temp,
    )


class TestPersonDetection:
    """
    Person detection is based on total FSR pressure sum (NOT temperature).
    The temperature sensor measures ambient room temperature (~20 degrees C) so it
    cannot be used to detect if someone is sitting.
    """

    def setup_method(self):
        # Default threshold = 1000 (total ADC sum across 4 sensors)
        self.proc = Preprocessor(fsr_presence_threshold=1000)

    def test_person_present_high_pressure(self):
        """High FSR values (person seated ~2500 per sensor) -> detected."""
        reading = make_reading(fl=2500, fm=2500, fr=2500, ml=2500, mc=2500, mr=2500, bl=2500, bm=2500, br=2500)
        assert self.proc.is_person_present(reading) is True

    def test_person_present_real_hardware_values(self):
        """Real observed seated values -> detected."""
        reading = make_reading(fl=2788, fr=3052, bl=2590, br=2428)
        assert self.proc.is_person_present(reading) is True

    def test_no_person_zero_pressure(self):
        """All zeros (sensors disconnected) -> not detected."""
        reading = make_reading(fl=0, fm=0, fr=0, ml=0, mc=0, mr=0, bl=0, bm=0, br=0)  # total=0
        assert self.proc.is_person_present(reading) is False

    def test_no_person_very_low_pressure(self):
        """Total below threshold -> not detected."""
        reading = make_reading(fl=100, fm=100, fr=100, ml=100, mc=100, mr=100, bl=100, bm=100, br=100)  # total=900
        assert self.proc.is_person_present(reading) is False

    def test_exactly_at_threshold(self):
        """Total exactly at threshold -> detected."""
        reading = make_reading(fl=250, fm=0, fr=250, ml=0, mc=0, mr=0, bl=250, bm=0, br=250)
        assert self.proc.is_person_present(reading) is True

    def test_custom_threshold(self):
        """Higher custom threshold requires more pressure."""
        proc = Preprocessor(fsr_presence_threshold=25000)
        reading = make_reading(fl=2788, fr=3052, bl=2590, br=2428)
        assert proc.is_person_present(reading) is False

    def test_temperature_does_not_affect_detection(self):
        """Temperature value is irrelevant for person detection."""
        high_pressure_cold = make_reading(fl=2500, fm=2500, fr=2500, ml=2500, mc=2500, mr=2500, bl=2500, bm=2500, br=2500, temp=10.0)
        high_pressure_hot  = make_reading(fl=2500, fm=2500, fr=2500, ml=2500, mc=2500, mr=2500, bl=2500, bm=2500, br=2500, temp=40.0)
        assert self.proc.is_person_present(high_pressure_cold) is True
        assert self.proc.is_person_present(high_pressure_hot)  is True


class TestFeatureExtraction:
    def setup_method(self):
        self.proc = Preprocessor()

    def test_output_shape(self):
        reading = make_reading()
        features = self.proc.extract_features(reading)
        assert features.shape == (9,)

    def test_output_dtype_float32(self):
        features = self.proc.extract_features(make_reading())
        assert features.dtype == np.float32

    def test_values_in_unit_range(self):
        reading = make_reading(fl=0, fr=4095, bl=2048, br=1024)
        features = self.proc.extract_features(reading)
        assert features.min() >= 0.0
        assert features.max() <= 1.0

    def test_zero_input_gives_zero(self):
        reading = make_reading(fl=0, fm=0, fr=0, ml=0, mc=0, mr=0, bl=0, bm=0, br=0)
        features = self.proc.extract_features(reading)
        np.testing.assert_array_equal(features, np.zeros(9, dtype=np.float32))

    def test_relative_percentage_equal_distibution(self):
        reading = make_reading(fl=4000, fm=4000, fr=4000, ml=4000, mc=4000, mr=4000, bl=4000, bm=4000, br=4000)
        features = self.proc.extract_features(reading)
        expected = np.array([1/9]*9, dtype=np.float32)
        np.testing.assert_allclose(features, expected, atol=1e-5)

    def test_order_fl_fr_bl_br(self):
        """Verify the feature vector order matches [fl, fm, fr, ml, mc, mr, bl, bm, br]."""
        reading = make_reading(fl=4095, fm=0, fr=0, ml=0, mc=0, mr=0, bl=0, bm=0, br=0)
        features = self.proc.extract_features(reading)
        assert features[0] == pytest.approx(1.0, abs=1e-4)
        assert features[1] == pytest.approx(0.0, abs=1e-4)
        assert features[2] == pytest.approx(0.0, abs=1e-4)
        assert features[3] == pytest.approx(0.0, abs=1e-4)

    def test_fsr_threshold_stored(self):
        proc = Preprocessor(fsr_presence_threshold=5000)
        assert proc._fsr_threshold == 5000
