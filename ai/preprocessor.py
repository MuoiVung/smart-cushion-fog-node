"""
Sensor data preprocessor for the Smart Cushion Fog Node.

Responsibilities:
  1. Extract raw FSR values as a numpy array suitable for the InferenceEngine.
  2. Temperature is passed through for reporting only — NOT used for
     person/occupancy detection (AI model handles that via FSR pressure).

Design notes (system_architecture.md §1):
  - The AI model outputs 11 labels including `empty` and `object`, which
    fully replaces any threshold-based person detection.
  - Temperature sensor measures ambient room temperature; it cannot reliably
    detect body heat from a cushion sensor position.
  - Raw ADC values (0–4095) are passed directly to InferenceEngine; the
    sklearn MinMaxScaler inside the engine handles normalisation.
"""

import logging
import numpy as np
from data.schema import AggregatedSensorReading

logger = logging.getLogger(__name__)

# FSR sensor order used by the model (must match training column order)
_FSR_KEYS = [
    "fsr_front_left",  "fsr_front_mid",  "fsr_front_right",
    "fsr_mid_left",    "fsr_mid_mid",    "fsr_mid_right",
    "fsr_back_left",   "fsr_back_mid",   "fsr_back_right",
]


class Preprocessor:
    """
    Converts AggregatedSensorReading objects into raw feature vectors for
    the InferenceEngine.  Occupancy / person-detection is entirely handled
    by the AI model — this class is intentionally logic-free.
    """

    def __init__(
        self,
        temperature_threshold: float = 30.0,   # kept for API compatibility, not used
        fsr_presence_threshold: int = 1000,     # kept for API compatibility, not used
    ) -> None:
        """
        Args:
            temperature_threshold:  Deprecated. Retained for backwards compatibility.
                                    Temperature is no longer used for person detection.
            fsr_presence_threshold: Deprecated. Retained for backwards compatibility.
                                    Occupancy detection is fully handled by the AI model.
        """
        # Parameters retained for callers that still pass them; not used internally
        _ = temperature_threshold
        _ = fsr_presence_threshold
        logger.info("Preprocessor initialised (occupancy detection → AI model)")

    # ── Public API ─────────────────────────────────────────────────────────

    def extract_raw(self, sensors: AggregatedSensorReading) -> np.ndarray:
        """
        Return a raw ADC array of shape (9,) for the InferenceEngine.

        The array order matches the training data columns:
          [FL, FM, FR, ML, MM, MR, BL, BM, BR]

        The InferenceEngine's sklearn scaler handles MinMax normalisation
        before feeding the values into the CNN.

        Args:
            sensors: Validated AggregatedSensorReading from the ESP32.

        Returns:
            numpy float32 array of shape (9,) with values in [0, 4095].
        """
        raw = np.array(
            [getattr(sensors, k) for k in _FSR_KEYS],
            dtype=np.float32,
        )
        logger.debug(f"Raw FSR array: {raw.astype(int).tolist()}  sum={int(raw.sum())}")
        return raw

    def get_temperature(self, sensors: AggregatedSensorReading) -> float:
        """
        Return the temperature reading for reporting purposes only.

        Temperature is NOT used for occupancy detection.

        Returns:
            Temperature in °C (float, 0–100).
        """
        return round(sensors.temperature, 1)

    # ── Deprecated compatibility shim ─────────────────────────────────────

    def is_person_present(self, sensors: AggregatedSensorReading) -> bool:
        """
        DEPRECATED: Occupancy is now determined by the AI model (PostureLabel).

        This method is retained so that older callers don't break.
        It always returns True when total FSR pressure > 1000 ADC units,
        but the actual occupancy decision should come from the AI label.
        """
        logger.warning(
            "is_person_present() is deprecated. Use InferenceEngine.predict() "
            "and check PostureLabel for empty/object/posture state."
        )
        total = sum(getattr(sensors, k) for k in _FSR_KEYS)
        return total >= 1000

    def extract_features(self, sensors: AggregatedSensorReading) -> np.ndarray:
        """
        DEPRECATED: Use extract_raw() instead.

        Kept for backwards compatibility. Returns raw ADC values (previously
        returned relative proportions, but the new Keras engine needs raw ADC).
        """
        return self.extract_raw(sensors)
