"""Tests for data schema validation."""

import pytest
from pydantic import ValidationError
from data.schema import (
    SensorReading, AggregatedSensorReading, RawMessage, ControlCommand,
    CloudSyncPayload, PostureCounts, PostureLabel,
)


# ── SensorReading ──────────────────────────────────────────────────────────

class TestSensorReading:
    def test_valid_reading(self):
        s = SensorReading(
            fsr_front_left=512, fsr_front_right=498,
            fsr_back_left=601, fsr_back_right=587,
            temperature=36.4,
        )
        assert s.fsr_front_left == 512
        assert s.temperature == 36.4
        assert s.fsr_front_mid is None

    def test_fsr_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            SensorReading(
                fsr_front_left=9999,  # > 4095
                fsr_front_right=0, fsr_back_left=0, fsr_back_right=0,
                temperature=36.0,
            )

    def test_negative_fsr_raises(self):
        with pytest.raises(ValidationError):
            SensorReading(
                fsr_front_left=-1,
                fsr_front_right=0, fsr_back_left=0, fsr_back_right=0,
                temperature=36.0,
            )

    def test_temperature_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            SensorReading(
                fsr_front_left=0, fsr_front_right=0,
                fsr_back_left=0, fsr_back_right=0,
                temperature=200.0,  # > 125
            )

    def test_boundary_values_accepted(self):
        s = SensorReading(
            fsr_front_left=0, fsr_front_right=4095,
            fsr_back_left=2048, fsr_back_right=1,
            temperature=-40.0,
        )
        assert s.fsr_front_right == 4095

class TestAggregatedSensorReading:
    def test_default_values(self):
        s = AggregatedSensorReading()
        assert s.fsr_front_left == 0
        assert s.temperature == 25.0

    def test_valid_reading(self):
        s = AggregatedSensorReading(
            fsr_front_left=512, fsr_front_mid=510, fsr_front_right=498,
            fsr_mid_left=500, fsr_mid_mid=500, fsr_mid_right=500,
            fsr_back_left=601, fsr_back_mid=590, fsr_back_right=587,
            temperature=36.4,
        )
        assert s.fsr_front_left == 512
        assert s.fsr_mid_mid == 500
        assert s.temperature == 36.4

    def test_negative_fsr_raises(self):
        with pytest.raises(ValidationError):
            AggregatedSensorReading(fsr_front_left=-1)


# ── RawMessage ─────────────────────────────────────────────────────────────

class TestRawMessage:
    VALID_SENSORS = dict(
        fsr_front_left=500, fsr_front_right=500,
        fsr_back_left=500, fsr_back_right=500,
        temperature=36.5,
    )

    def test_valid_message(self):
        msg = RawMessage(
            device_id="esp32-01",
            timestamp=1712345678.0,
            sensors=self.VALID_SENSORS,
        )
        assert msg.device_id == "esp32-01"
        assert msg.sensors.fsr_front_left == 500
        assert msg.sensors.fsr_mid_mid is None

    def test_missing_field_raises(self):
        with pytest.raises(ValidationError):
            RawMessage(device_id="esp32-01", timestamp=123.0)  # missing sensors


# ── ControlCommand ─────────────────────────────────────────────────────────

class TestControlCommand:
    def test_defaults(self):
        cmd = ControlCommand()
        assert cmd.command == "vibrate"
        assert cmd.duration_ms == 1000

    def test_duration_too_short_raises(self):
        with pytest.raises(ValidationError):
            ControlCommand(duration_ms=50)  # < 100 ms

    def test_custom_command(self):
        cmd = ControlCommand(duration_ms=2000, reason="lean_left")
        assert cmd.reason == "lean_left"


# ── CloudSyncPayload ───────────────────────────────────────────────────────

class TestCloudSyncPayload:
    def test_valid_payload(self):
        p = CloudSyncPayload(
            device_id="esp32-01",
            window_start=1000.0,
            window_end=1060.0,
            correct_seconds=45.0,
            incorrect_seconds=15.0,
            posture_counts=PostureCounts(correct=9, lean_left=2),
        )
        assert p.correct_seconds == 45.0
        assert p.posture_counts.lean_left == 2

    def test_negative_seconds_raises(self):
        with pytest.raises(ValidationError):
            CloudSyncPayload(
                device_id="x",
                window_start=0, window_end=60,
                correct_seconds=-1.0,   # invalid
                incorrect_seconds=0,
                posture_counts=PostureCounts(),
            )
