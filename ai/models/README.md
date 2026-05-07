# AI Model Weights & Scalers

Place your trained Keras model files and scikit-learn scalers here.

## 📂 Expected Formats

### 1. Keras Model (`.h5` or `.keras`)
- **Architecture**: 2D CNN (usually expects a 3x3 input matrix).
- **Input**: Normalised FSR values (9 sensors).
- **Output**: Posture logits/probabilities.

### 2. Scaler (`.pkl`)
- **Format**: `sklearn.preprocessing.MinMaxScaler` exported via `joblib`.
- **Purpose**: Normalises raw ADC values (0–4095) to the range used during training (usually 0–1).

---

## 🛠 Model Management

The Fog Node uses a **Local Database** as the primary source of truth for model paths.
- You can change models via the **Launcher UI** under "Config & Control".
- **Hot-Reload**: Changing models via the UI updates the running engine immediately without a restart.

### Filename Naming Convention
For auto-detection to work in the Launcher, use matching suffixes:
- Model: `posture_9_model_variantName.h5`
- Scaler: `fsr_scaler_9_variantName.pkl`

---

## ⚙️ Training Data Flow
1. **Raw**: 9 sensors → ADC [0-4095].
2. **Preprocess**: Reshape to (1, 3, 3, 1).
3. **Scale**: Apply `.pkl` scaler.
4. **Predict**: Inference via `.h5` model.

---

## ⚠️ FALLBACK
If no model is found or loading fails, the system uses a **Rule-based Stub** which uses FSR symmetry heuristics to detect occupancy and basic postures.
