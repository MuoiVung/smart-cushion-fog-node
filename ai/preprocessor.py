"""
Sensor data preprocessor for the Smart Cushion Fog Node.

Responsibilities:
  1. Detect human presence from the infrared temperature reading.
  2. Normalize raw FSR ADC values into a [0, 1] float vector suitable
     for the PyTorch inference model.

Design notes:
  - FSR normalization is simple min-max scaling against the ADC full-scale
    (0–4095 for ESP32 12-bit ADC). Future versions can add per-sensor
    calibration offsets stored in a calibration.json file.
  - Temperature thresholding is intentionally simple; a more sophisticated
    approach could use a short rolling average to avoid false negatives
    caused by brief sensor glitches.
"""

import logging
import numpy as np
from data.schema import SensorReading

logger = logging.getLogger(__name__)

# Full-scale ADC value for ESP32 (12-bit → 4095)
_FSR_MAX = 4095.0


class Preprocessor:
    """
    Converts raw SensorReading objects into normalised feature vectors
    ready for the AI inference engine.

    Hardware context:
      - FSR402 sensors output raw ADC values (0–4095, 12-bit ESP32).
      - Baseline (unloaded) observed at ~2400–3100 due to sensor + circuit offset.
      - Temperature (pre-converted to °C on ESP32): ~20–25°C empty, ~32–37°C seated.
    """

    def __init__(self, temperature_threshold: float = 30.0) -> None:
        """
        Args:
            temperature_threshold: If the measured temperature is below this
                value (°C), the seat is considered empty and posture
                classification is skipped.
        """
        self._temp_threshold = temperature_threshold
        logger.info(
            f"Preprocessor initialised: temperature_threshold={temperature_threshold}°C, "
            f"fsr_max={int(_FSR_MAX)}"
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def is_person_present(self, sensors: SensorReading) -> bool:
        """
        Determine whether a human is sitting on the cushion.

        Uses the IR sensor temperature reading: human body temperature
        is typically 36–37 °C, while an empty cushion is close to
        room temperature (20–25 °C).

        Returns:
            True  → a person is present, proceed with posture classification.
            False → seat is empty, skip inference and send no alerts.
        """
        present = sensors.temperature >= self._temp_threshold
        if not present:
            logger.debug(
                f"No person detected: temperature={sensors.temperature:.1f}°C "
                f"(threshold={self._temp_threshold}°C) "
                f"– empty cushion ≈ room temp, seated ≈ 32–37°C"
            )
        return present

    def extract_features(self, sensors: SensorReading) -> np.ndarray:
        """
        Convert raw FSR ADC values into a normalised feature vector.

        The returned array has shape (4,) with values in [0.0, 1.0]:
            [fsr_front_left, fsr_front_right, fsr_back_left, fsr_back_right]

        Args:
            sensors: Validated SensorReading from the ESP32.

        Returns:
            numpy float32 array of shape (4,).
        """
        raw = np.array(
            [
                sensors.fsr_front_left,
                sensors.fsr_front_right,
                sensors.fsr_back_left,
                sensors.fsr_back_right,
            ],
            dtype=np.float32,
        )

        # Min-max normalise against full ADC scale
        normalised = raw / _FSR_MAX

        # Clip to [0, 1] as a safety guard against out-of-range readings
        normalised = np.clip(normalised, 0.0, 1.0)

        logger.debug(f"Features (normalised): {normalised}")
        return normalised
