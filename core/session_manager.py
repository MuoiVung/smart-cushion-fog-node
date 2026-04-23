"""
Session Manager for the Smart Cushion Fog Node.

Tracks posture readings within a rolling time window and:
  - Decides when to trigger a vibration alert (N consecutive bad postures).
  - Aggregates per-window statistics for cloud sync.

Thread safety:
  All public methods are called from the asyncio event loop (single thread),
  so no explicit locking is required.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional, Tuple

from data.schema import (
    CloudSummaryRecord,
    PostureDurationBreakdown,
    PostureLabel,
    SITTING_POSTURES,
    GOOD_POSTURES,
)
# Backwards-compatible alias
CloudSyncPayload = CloudSummaryRecord

logger = logging.getLogger(__name__)


@dataclass
class _Reading:
    """Internal record stored per incoming sensor cycle."""
    timestamp:       float
    posture:         PostureLabel
    person_detected: bool


class SessionManager:
    """
    Rolling-window session tracker.

    The window starts from the most recent reading and extends back
    `window_seconds` into the past. Readings older than the window
    are automatically pruned on each update.

    Alert logic:
        A vibration alert is triggered when `incorrect_alert_threshold`
        consecutive INCORRECT or UNKNOWN readings accumulate (while a
        person is present). The counter resets as soon as a CORRECT
        reading is observed.
    """

    def __init__(
        self,
        window_seconds: int = 60,
        incorrect_alert_threshold: int = 3,
    ) -> None:
        """
        Args:
            window_seconds:             Duration of the rolling window (seconds).
            incorrect_alert_threshold:  Consecutive wrong readings before alert fires.
        """
        self._window_seconds = window_seconds
        self._alert_threshold = incorrect_alert_threshold

        self._readings: Deque[_Reading] = deque()
        self._consecutive_incorrect: int = 0

        # Timestamps used to build CloudSyncPayload.window_start
        self._window_start: float = time.time()

        logger.info(
            f"SessionManager initialised: window={window_seconds}s, "
            f"alert_threshold={incorrect_alert_threshold} consecutive readings"
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def add_reading(
        self,
        posture: PostureLabel,
        person_detected: bool,
        timestamp: Optional[float] = None,
    ) -> bool:
        """
        Record a new posture reading and evaluate whether an alert is needed.

        Args:
            posture:         Classified posture label from InferenceEngine.
            person_detected: Result of Preprocessor.is_person_present().
            timestamp:       Unix timestamp of the reading (defaults to now).

        Returns:
            True  → a vibration alert should be sent to the ESP32.
            False → no alert needed this cycle.
        """
        ts = timestamp if timestamp is not None else time.time()
        reading = _Reading(timestamp=ts, posture=posture, person_detected=person_detected)
        self._readings.append(reading)
        self._prune_old_readings(ts)

        should_alert = self._evaluate_alert(reading)
        return should_alert

    def get_sync_payload(self, device_id: str) -> CloudSyncPayload:
        """
        Build a CloudSyncPayload from readings in the current window.

        Should be called every CLOUD_SYNC_INTERVAL seconds.
        Resets the window start timestamp after building the payload.

        Args:
            device_id: Device identifier to embed in the payload.

        Returns:
            CloudSyncPayload ready to be published to AWS IoT Core.
        """
        now = time.time()
        window_end = now

        counts = PostureCounts()
        correct_time: float = 0.0
        incorrect_time: float = 0.0

        readings_in_window = [r for r in self._readings if r.person_detected]

        for i, reading in enumerate(readings_in_window):
            # Estimate duration of this reading as the gap to the next one
            if i + 1 < len(readings_in_window):
                duration = readings_in_window[i + 1].timestamp - reading.timestamp
            else:
                duration = 2.0  # Assume ~2 s for the last reading

            duration = min(duration, 10.0)  # Cap to avoid huge gaps

            match reading.posture:
                case PostureLabel.CORRECT:
                    counts.correct += 1
                    correct_time += duration
                case PostureLabel.LEAN_LEFT:
                    counts.lean_left += 1
                    incorrect_time += duration
                case PostureLabel.LEAN_RIGHT:
                    counts.lean_right += 1
                    incorrect_time += duration
                case PostureLabel.SLOUCH_FORWARD:
                    counts.slouch_forward += 1
                    incorrect_time += duration
                case PostureLabel.LEAN_BACK:
                    counts.lean_back += 1
                    incorrect_time += duration

        payload = CloudSyncPayload(
            device_id=device_id,
            window_start=self._window_start,
            window_end=window_end,
            correct_seconds=round(correct_time, 1),
            incorrect_seconds=round(incorrect_time, 1),
            posture_counts=counts,
        )

        # Roll the window forward
        self._window_start = now

        logger.info(
            f"Sync payload built: correct={payload.correct_seconds}s, "
            f"incorrect={payload.incorrect_seconds}s"
        )
        return payload

    # ── Private helpers ────────────────────────────────────────────────────

    def _evaluate_alert(self, reading: _Reading) -> bool:
        """Update the consecutive-incorrect counter and decide whether to alert."""
        if not reading.person_detected:
            # Nobody sitting – reset counter
            self._consecutive_incorrect = 0
            return False

        if reading.posture == PostureLabel.CORRECT:
            self._consecutive_incorrect = 0
            return False

        # Posture is incorrect and person is present
        self._consecutive_incorrect += 1
        logger.debug(
            f"Consecutive incorrect: {self._consecutive_incorrect}/{self._alert_threshold}"
        )

        if self._consecutive_incorrect >= self._alert_threshold:
            # Reset counter so we don't spam alerts every reading
            self._consecutive_incorrect = 0
            logger.info(
                f"Alert threshold reached ({self._alert_threshold} consecutive). "
                f"Last posture: {reading.posture.value}"
            )
            return True

        return False

    def _prune_old_readings(self, now: float) -> None:
        """Remove readings older than the rolling window."""
        cutoff = now - self._window_seconds
        while self._readings and self._readings[0].timestamp < cutoff:
            self._readings.popleft()
