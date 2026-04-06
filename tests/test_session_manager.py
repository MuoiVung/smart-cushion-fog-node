"""Tests for SessionManager – alert logic and cloud sync aggregation."""

import time
import pytest
from core.session_manager import SessionManager
from data.schema import PostureLabel


@pytest.fixture
def manager():
    return SessionManager(window_seconds=60, incorrect_alert_threshold=3)


class TestAlertLogic:
    def test_correct_posture_no_alert(self, manager):
        assert manager.add_reading(PostureLabel.CORRECT, person_detected=True) is False

    def test_no_person_no_alert(self, manager):
        assert manager.add_reading(PostureLabel.LEAN_LEFT, person_detected=False) is False

    def test_consecutive_incorrect_triggers_alert(self, manager):
        """Alert fires exactly when the threshold is reached."""
        assert manager.add_reading(PostureLabel.LEAN_LEFT, True) is False   # 1st
        assert manager.add_reading(PostureLabel.LEAN_LEFT, True) is False   # 2nd
        assert manager.add_reading(PostureLabel.LEAN_LEFT, True) is True    # 3rd → alert

    def test_counter_resets_after_alert(self, manager):
        """After an alert fires the counter resets; next batch needs N wrong again."""
        for _ in range(3):
            manager.add_reading(PostureLabel.LEAN_RIGHT, True)
        # Counter should be reset; one more wrong reading should NOT alert
        assert manager.add_reading(PostureLabel.LEAN_RIGHT, True) is False

    def test_correct_reading_resets_counter(self, manager):
        manager.add_reading(PostureLabel.LEAN_LEFT, True)  # 1st incorrect
        manager.add_reading(PostureLabel.LEAN_LEFT, True)  # 2nd incorrect
        manager.add_reading(PostureLabel.CORRECT, True)    # reset
        # Need 3 more incorrect to trigger
        assert manager.add_reading(PostureLabel.LEAN_LEFT, True) is False
        assert manager.add_reading(PostureLabel.LEAN_LEFT, True) is False
        assert manager.add_reading(PostureLabel.LEAN_LEFT, True) is True  # triggers

    def test_unknown_posture_counts_as_incorrect(self, manager):
        """UNKNOWN (no person according to temp) should NOT alert."""
        for _ in range(5):
            result = manager.add_reading(PostureLabel.UNKNOWN, person_detected=False)
        # person_detected=False, so counter stays 0
        assert result is False


class TestSyncPayload:
    def test_payload_structure(self, manager):
        manager.add_reading(PostureLabel.CORRECT, True)
        manager.add_reading(PostureLabel.LEAN_LEFT, True)
        payload = manager.get_sync_payload("test-device")

        assert payload.device_id == "test-device"
        assert payload.window_start <= payload.window_end
        assert payload.correct_seconds >= 0
        assert payload.incorrect_seconds >= 0
        assert payload.posture_counts.correct >= 1
        assert payload.posture_counts.lean_left >= 1

    def test_payload_resets_window_start(self, manager):
        t1 = manager._window_start
        time.sleep(0.05)
        manager.get_sync_payload("x")
        t2 = manager._window_start
        assert t2 > t1

    def test_empty_session_payload(self, manager):
        payload = manager.get_sync_payload("empty")
        assert payload.correct_seconds == 0.0
        assert payload.incorrect_seconds == 0.0
