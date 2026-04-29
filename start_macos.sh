#!/bin/zsh

# Smart Cushion Fog Node - macOS/Linux Setup Script

echo "=============================================================="
echo "Smart Cushion Fog Node - macOS Native Setup"
echo "=============================================================="

# Check for Python installation
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "[ERROR] Python is not installed. Please install Python 3."
    exit 1
fi

echo "[INFO] Using Python command: $PYTHON_CMD"

# Check if .env file exists
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        echo "[INFO] Creating .env from .env.example"
        cp .env.example .env
    else
        echo "[WARNING] .env.example not found. Please create a .env file manually."
    fi
fi

# Create Virtual Environment
if [ ! -d "venv" ]; then
    echo "[INFO] Creating Python Virtual Environment..."
    $PYTHON_CMD -m venv venv
fi

# Activate Virtual Environment
echo "[INFO] Activating Virtual Environment..."
source venv/bin/activate

# Install Requirements
echo "[INFO] Upgrading pip..."
pip install --upgrade pip -q

echo "[INFO] Installing main dependencies..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt -q
fi

echo "[INFO] Installing launcher dependencies..."
if [ -f "launcher/requirements.txt" ]; then
    pip install -r launcher/requirements.txt -q
fi

echo "[INFO] Setup complete! Starting the application..."
echo "=============================================================="

# Run the application
$PYTHON_CMD run_launcher.py
