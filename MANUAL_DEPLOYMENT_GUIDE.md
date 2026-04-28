# Manual Deployment & Build Guide

This guide explains how to package the Smart Cushion Fog Node into a standalone executable and how to update the ESP32 edge devices to connect to a new machine.

## 1. Packaging the Fog Node (Desktop App)

You can build a standalone version of the app that runs without needing Python or Docker installed on the target machine.

### Prerequisites
- Install Python 3.10+
- Open a terminal in the `smart-cushion-fog` directory.
- Install the required libraries:
  ```bash
  pip install -r requirements.txt
  pip install pyinstaller
  ```

### Build Instructions
Run the following command based on your Operating System:

#### Windows
```bash
pyinstaller --noconfirm --onedir --windowed --add-data "launcher/saved_labels.json;launcher" app.py
```

#### macOS / Linux
```bash
pyinstaller --noconfirm --onedir --windowed --add-data "launcher/saved_labels.json:launcher" app.py
```

### Output
After the build completes, a `dist/` folder will be created.
- You can copy the entire `dist/app` folder (or `app.exe` folder) to any other machine.
- To run the app, simply execute `app.exe` (Windows) or the `app` binary (Mac/Linux).

---

## 2. Updating Edge Devices (ESP32)

When you move the Fog Node to a new computer, its **IP Address** will change. You MUST update the ESP32 firmware to point to the new IP.

### Step 1: Find the New Fog Node IP
On the machine running the Fog Node app:
- **Windows:** Open Command Prompt and type `ipconfig`. Look for "IPv4 Address".
- **Mac/Linux:** Open Terminal and type `ifconfig` or `ip addr`.

### Step 2: Update `esp32_secrets.h`
1. Open the `smart-cushion-edge` repository.
2. Open the file `esp32_secrets.h`.
3. Locate line 12 and update it with the new IP:
   ```cpp
   // Update this with the new IP address of your Fog Node machine
   const char *mqtt_server = "192.168.1.XX"; 
   ```

### Step 3: Re-flash the ESP32
1. Connect the ESP32 to your computer via USB.
2. Use **Arduino IDE** or **VS Code (PlatformIO)** to open the firmware folder (e.g., `esp32_1_firmware`).
3. Select the correct Board (ESP32 Dev Module) and Port.
4. Click **Upload** to flash the new configuration to the ESP32.

---

## 3. Deployment FAQ

**Q: Can I just send the built file to another machine?**
**A:** Yes! Once you have the `dist/app` folder, you can zip it and send it to any other machine of the same OS. They don't need to install Python or clone the code.

**Q: Does the target machine need Docker?**
**A:** No. This standalone build runs directly on the OS.

**Q: What if the app doesn't start on the new machine?**
**A:** Ensure you copied the **entire** folder inside `dist/`, not just the `.exe` file, as the app depends on the DLLs and data files located in that folder.
