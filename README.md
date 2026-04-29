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
    # Update MQTT and Model paths in .env
    ```

2.  **Requirements**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run Launcher (UI)**:
    ```bash
    python run_launcher.py
    ```

## 🧠 AI Features
-   **9-Posture Classifier**: Recognizes 9 human sitting postures (NUP, LF, LB, etc.).
-   **Smart Empty Detection**: Automatically detects empty cushion state when total sensor pressure < 1000.
-   **Keras CNN 2D**: Processes a 3x3 FSR pressure matrix for high-accuracy classification.

---
© 2026 ErgoVita Team
