# ErgoVita - Smart Cushion Fog Node

The "Brain" of the Smart Cushion system. This node aggregates raw sensor data from ESP32 edge devices, performs AI-based posture classification using a Keras CNN model, and broadcasts results to the user-facing app and cloud.

---

## 📂 Project Documentation

To keep the repository clean, all technical documentation has been moved to the `docs/` folder:

1.  **[Data Structures & Interfaces](docs/DATA_STRUCTURES.md)**: Detailed JSON formats for Cloud, ESP32, and Web App communication.
2.  **[System Architecture](docs/system_architecture.md)**: Overall system design, AI label classification, and logic flow.
3.  **[Manual Deployment Guide](docs/MANUAL_DEPLOYMENT_GUIDE.md)**: How to set up the Fog Node on a new machine.
4.  **[Windows Installation](docs/WINDOWS_INSTALL.md)**: Specific instructions for Windows users.

---

## 🚀 Quick Start

1.  **Environment**:
    ```bash
    cp .env.example .env
    # Update AWS credentials and initial paths in .env
    ```

2.  **Requirements**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run Launcher (UI)**:
    ```bash
    python run_launcher.py
    ```

---

## ✨ New Features (May 2026)

### 🔥 AI Hot-Reload
Change AI models on the fly without restarting the Fog Node or Docker containers.
- **Auto-Detection**: When you select a `.h5` model, the Launcher automatically finds the matching `.pkl` scaler.
- **MQTT Trigger**: Updates are pushed to the running engine in ~2 seconds.

### 📦 Local SQLite Persistence
A new `data/fog_local.db` file stores:
- **Config Store**: AI model paths are now saved in a database, making `.env` safer from manual errors.
- **Offline Cloud Queue**: If the internet goes down, AWS IoT events are buffered locally and automatically retried when connectivity is restored.

---

## 🧠 AI Pipeline
-   **Model**: Keras CNN 2D (Processes 3x3 FSR matrix).
-   **Inputs**: 9 raw ADC values (0–4095) from FSR sensors.
-   **Normalization**: Handled by an `sklearn` MinMaxScaler (.pkl) inside the engine.
-   **Labels**: Detects 9-11 postures including `Empty`, `Object`, and specific leanings.

---
© 2026 ErgoVita Team
