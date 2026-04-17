"""
Sensor data preprocessor for the Smart Cushion Fog Node.

Responsibilities:
  1. Detect human presence using total FSR pressure (sum of all 4 sensors).
  2. Convert raw FSR pressure to relative percentages (0.0 to 1.0) so the
     model is independent of the person's absolute body weight.

Design notes:
  - Person detection uses total FSR sum, NOT temperature.
    Reason: the temperature sensor measures ambient room temperature (~20-25°C)
    and does NOT detect body heat, so it cannot reliably determine if someone
    is seated.
  - FSR feature extraction calculates relative proportion (each sensor / total pressure).
    This makes the AI inference robust to different occupant weights.
  - The fsr_presence_threshold (default: 1000) should be tuned based on the
    observed empty-cushion FSR readings for your specific hardware.
"""

import logging
import numpy as np
from data.schema import AggregatedSensorReading

logger = logging.getLogger(__name__)

# Full-scale ADC value for ESP32 (12-bit -> 4095)
_FSR_MAX = 4095.0


class Preprocessor:
    """
    Converts aggregated SensorReadingState objects into normalised feature vectors
    ready for the AI inference engine.

    Hardware context:
      - FSR402 sensors output raw ADC values (0-4095, 12-bit ESP32).
      - Observed values with person seated: ~2400-3100 per sensor.
      - Temperature sensor measures AMBIENT temperature (room, ~20-25 degrees C),
        not body temperature -- cannot be used for person detection.
    """

    def __init__(
        self,
        fsr_presence_threshold: int = 1000,
        temperature_threshold: float = 30.0,   # kept for backward compat, unused
    ) -> None:
        """
        Args:
            fsr_presence_threshold: Minimum total FSR ADC sum (all 9 sensors)
                to consider a person as seated. Default 1000 is conservative
                -- tune higher if the empty-cushion baseline sum is above this.
            temperature_threshold: Deprecated. Kept for compatibility but no
                longer used for person detection (temperature sensor measures
                ambient air, not body heat).
        """
        self._fsr_threshold = fsr_presence_threshold
        self._temp_threshold = temperature_threshold   # retained, unused
        logger.info(
            f"Preprocessor initialised: fsr_presence_threshold={fsr_presence_threshold}, "
            f"fsr_max={int(_FSR_MAX)}"
        )

    # -- Public API -----------------------------------------------------------

    def is_person_present(self, sensors: AggregatedSensorReading) -> bool:
        """
        Determine whether a human is sitting on the cushion.

        Uses the SUM of all 9 FSR sensor readings as the pressure indicator.
        A person is considered present when:
            fsr_front_left + fsr_front_mid + ... + fsr_back_right
            >= fsr_presence_threshold

        NOTE: Temperature is NOT used here because the onboard temperature
        sensor measures ambient room temperature (~20 degrees C), not body surface
        temperature. Total FSR pressure is a more reliable presence signal.

        Returns:
            True  -> a person is present, proceed with posture classification.
            False -> seat is empty, skip inference and send no alerts.
        """
        total_pressure = (
            sensors.fsr_front_left
            + sensors.fsr_front_mid
            + sensors.fsr_front_right
            + sensors.fsr_mid_left
            + sensors.fsr_mid_mid
            + sensors.fsr_mid_right
            + sensors.fsr_back_left
            + sensors.fsr_back_mid
            + sensors.fsr_back_right
        )
        present = total_pressure >= self._fsr_threshold
        if not present:
            logger.debug(
                f"No person detected: total FSR={total_pressure} "
                f"(threshold={self._fsr_threshold})"
            )
        else:
            logger.debug(
                f"Person detected: total FSR={total_pressure} "
                f"(threshold={self._fsr_threshold})"
            )
        return present

    def extract_features(self, sensors: AggregatedSensorReading) -> np.ndarray:
        """
        Convert raw FSR pressure into a weight-independent relative percentage vector.

        The returned array has shape (9,) with values representing the percentage
        of pressure on each sensor:
            [fl, fm, fr, ml, mm, mr, bl, bm, br]
        The sum of the features will be exactly 1.0 if a person is seated.

        Args:
            sensors: Validated AggregatedSensorReading from the ESP32.

        Returns:
            numpy float32 array of shape (9,).
        """
        raw = np.array(
            [
                sensors.fsr_front_left,
                sensors.fsr_front_mid,
                sensors.fsr_front_right,
                sensors.fsr_mid_left,
                sensors.fsr_mid_mid,
                sensors.fsr_mid_right,
                sensors.fsr_back_left,
                sensors.fsr_back_mid,
                sensors.fsr_back_right,
            ],
            dtype=np.float32,
        )

        total_pressure = raw.sum()
        if total_pressure > 0:
            features = raw / total_pressure
        else:
            features = np.zeros(9, dtype=np.float32)

        # Clip to [0, 1] as a safety guard
        features = np.clip(features, 0.0, 1.0)

        logger.debug(f"Features (relative %): {features}  total_raw={int(total_pressure)}")
        return features
