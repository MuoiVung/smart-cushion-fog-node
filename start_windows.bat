@echo off
setlocal

echo ==============================================================
echo Smart Cushion Fog Node - Windows Native Setup (Without Docker)
echo ==============================================================

:: Check for Python installation
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in the system PATH.
    echo Please install Python 3.9 or higher from https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Check if .env file exists, if not copy from example
if not exist .env (
    if exist .env.example (
        echo [INFO] .env file not found. Creating one from .env.example...
        copy .env.example .env
        echo [INFO] A default .env file has been created. Please edit it with your keys later!
    )
)

:: Create Virtual Environment if it doesn't exist
IF NOT EXIST "venv" (
    echo [INFO] Creating Python Virtual Environment (venv)...
    python -m venv venv
) ELSE (
    echo [INFO] Python Virtual Environment already exists.
)

:: Activate Virtual Environment
echo [INFO] Activating Virtual Environment...
call venv\Scripts\activate.bat

:: Install Requirements
echo [INFO] Upgrading pip...
python -m pip install --upgrade pip >nul

echo [INFO] Installing main dependencies...
pip install -r requirements.txt

echo [INFO] Installing launcher dependencies...
pip install -r launcher\requirements.txt

echo [INFO] Setup complete! Starting the application...
echo ==============================================================

:: Run the application
python run_launcher.py

pause
