# ErgoVita - Smart Cushion Fog Node

The "Brain" of the Smart Cushion system. This node aggregates raw sensor data from ESP32 edge devices, performs AI-based posture classification using a Keras CNN model, and broadcasts results to the user-facing app.

## 🧠 AI Features
-   **11-Class Classifier**: Recognizes 9 human sitting postures + `EMPTY` (cushion empty) + `OBJECT` (non-human object).
-   **Keras CNN 2D**: Processes 3x3 FSR pressure matrix.
-   **No Temperature Relying**: Occupancy detection is handled entirely by AI pressure analysis.

## 🚀 Setup

1.  **Environment**:
    ```bash
    cp .env.example .env
    # Update MQTT and Model paths if necessary
    ```

2.  **Model Files**:
    Ensure `smart_cushion_model.h5` and `fsr_scaler.pkl` are located in `ai/models/`.

3.  **Run**:
    ```bash
    pip install -r requirements.txt
    python app.py
    ```

## 📡 Interfaces
-   **Interface 01 (MQTT)**: Receives raw FSR telemetry from Edge.
-   **Interface 02 (WebSocket)**: Broadcasts real-time posture and heatmap to the Web App.
-   **Interface 03 (AWS IoT)**: Syncs session summaries to the cloud for historical analysis.
-   **Interface 05 (MQTT)**: Sends vibration commands back to the Edge.
