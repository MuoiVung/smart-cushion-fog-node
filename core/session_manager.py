"""
Session Manager for the Smart Cushion Fog Node.

Tracks posture readings within a single session and:
  - Aggregates per-session statistics for cloud sync (CloudSummaryRecord).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from data.schema import (
    CloudSummaryRecord,
    PostureDurationBreakdown,
    PostureLabel,
    GOOD_POSTURES,
)

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Tracks statistics for an active sitting session.
    Calculates total durations and a breakdown for all 9 postures.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._session_id: str = ""
        self._start_time: Optional[datetime] = None
        self._last_reading_time: Optional[float] = None
        
        self._total_sitting_duration_sec: float = 0.0
        self._poor_posture_duration_sec: float = 0.0
        
        # Breakdown counters for each posture
        self._durations = {
            PostureLabel.NUP: 0.0,
            PostureLabel.LF: 0.0,
            PostureLabel.LB: 0.0,
            PostureLabel.LFSR: 0.0,
            PostureLabel.LFSL: 0.0,
            PostureLabel.CRL: 0.0,
            PostureLabel.CLL: 0.0,
            PostureLabel.CRLL: 0.0,
            PostureLabel.CLLL: 0.0,
        }

    # ── Public API ─────────────────────────────────────────────────────────

    def start_session(self, session_id: str, start_time: datetime, current_ts: float) -> None:
        self.reset()
        self._session_id = session_id
        self._start_time = start_time
        self._last_reading_time = current_ts

    def add_reading(self, posture: PostureLabel, current_ts: float) -> None:
        """
        Record a posture and calculate duration since the last reading.
        Must only be called when a session is active and person is sitting.
        """
        if not self._session_id or self._last_reading_time is None:
            return

        duration = current_ts - self._last_reading_time
        duration = min(duration, 5.0)  # Cap at 5s to ignore long gaps

        self._last_reading_time = current_ts

        if posture in self._durations:
            self._durations[posture] += duration
            self._total_sitting_duration_sec += duration
            
            if posture not in GOOD_POSTURES:
                self._poor_posture_duration_sec += duration

    def get_poor_posture_duration_sec(self) -> int:
        return int(self._poor_posture_duration_sec)

    def get_good_posture_pct(self) -> int:
        if self._total_sitting_duration_sec <= 0:
            return 100
        good_sec = self._total_sitting_duration_sec - self._poor_posture_duration_sec
        return int((good_sec / self._total_sitting_duration_sec) * 100)

    def get_posture_distribution(self) -> dict[str, int]:
        return {label.value: int(sec) for label, sec in self._durations.items()}

    def get_summary(self, device_id: str, end_time_iso: str, alert_count: int) -> CloudSummaryRecord:
        """
        Build a CloudSummaryRecord from the tracked statistics.
        """
        breakdown = PostureDurationBreakdown(
            nup_duration_sec=int(self._durations[PostureLabel.NUP]),
            lf_duration_sec=int(self._durations[PostureLabel.LF]),
            lb_duration_sec=int(self._durations[PostureLabel.LB]),
            lfsr_duration_sec=int(self._durations[PostureLabel.LFSR]),
            lfsl_duration_sec=int(self._durations[PostureLabel.LFSL]),
            crl_duration_sec=int(self._durations[PostureLabel.CRL]),
            cll_duration_sec=int(self._durations[PostureLabel.CLL]),
            crll_duration_sec=int(self._durations[PostureLabel.CRLL]),
            clll_duration_sec=int(self._durations[PostureLabel.CLLL]),
        )

        return CloudSummaryRecord(
            device_id=device_id,
            session_id=self._session_id,
            fog_timestamp_iso=datetime.now(timezone.utc).isoformat(),
            start_time=self._start_time.isoformat() if self._start_time else "",
            end_time=end_time_iso,
            total_sitting_duration_sec=int(self._total_sitting_duration_sec),
            poor_posture_duration_sec=int(self._poor_posture_duration_sec),
            alert_count=alert_count,
            posture_duration_breakdown=breakdown,
        )
