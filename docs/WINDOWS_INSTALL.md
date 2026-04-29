# 🟡 Windows Installation Guide (Standalone / Non-Docker)

This document provides detailed instructions for installing and running the **Smart Cushion Fog Node** directly on Windows **without Docker**.

The process is fully automated via the `start_windows.bat` script. Follow the simple steps below to get started.

---

## 🛠 1. Prerequisites

Before starting, ensure your Windows machine has:
- **Python (Version 3.9 or higher):** 
  - Download from the official site: https://www.python.org/downloads/
  - **IMPORTANT:** During the Python installation, you **MUST** check the box **`"Add Python 3.X to PATH"`** on the first screen before clicking Install.

---

## 🚀 2. Installation & Execution (One-Click Setup)

### Step 1: Launch the Automation Script
1. Download or `git clone` this repository to your Windows machine.
2. Open the project folder.
3. Locate and double-click the file named **`start_windows.bat`**.

### Step 2: Automated Setup
A terminal window will open. The script will automatically perform the following:
- Verify Python installation.
- Create a **Virtual Environment (`venv`)** to keep your system clean.
- Create a **`.env`** file from the template (`.env.example`) if it doesn't exist.
- Install all required Python libraries.
- Launch the **Smart Cushion Fog Node Launcher** UI.

> **Note:** If the Launcher shows an MQTT connection error on the first run, don't worry! This is because your `.env` configuration (IP, credentials) is not yet configured for your local network. Proceed to Section 3.

---

## ⚙️ 3. Configuration (The `.env` File)

The system uses a **`.env`** file for configuration. If you need to change your network settings:

1. Close the Launcher if it's running.
2. Open the **`.env`** file in a text editor (e.g., Notepad, VS Code).
3. Update the following variables:

**MQTT Broker Settings:**
- **`MQTT_HOST`**: The IP address of your MQTT Broker (e.g., `192.168.1.100`) or a Ngrok URL (e.g., `0.tcp.ap.ngrok.io`).
- **`MQTT_PORT`**: Default is `1883`.
- **`MQTT_USERNAME`** & **`MQTT_PASSWORD`**: Credentials required to connect to your broker.

**App Dashboard Security:**
- **`WS_AUTH_TOKEN`**: A password for the Web Dashboard. Create a unique string (e.g., `MySecretToken123456`). You will be prompted for this token when opening the web interface.

4. **Save the file**.

---

## 🔄 4. Subsequent Runs

After the initial setup and configuration:
- Simply double-click **`start_windows.bat`** whenever you want to start the system.
- The script will detect the existing setup and launch the application immediately.

--- 

## ❓ Troubleshooting

- **`Python is not installed or not in the system PATH`**: Re-install Python and ensure you check the "Add to PATH" box.
- **Missing `requirements.txt`**: Ensure you have extracted all files from the repository zip or cloned the full repo.
- **MQTT Connection Issues**: Double-check your `MQTT_HOST` and credentials in the `.env` file. Ensure your ESP32 devices and the Fog Node are on the same network or accessible via your broker.
