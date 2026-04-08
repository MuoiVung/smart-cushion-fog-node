@echo off
setlocal
chcp 65001 >nul

echo ==============================================================
echo Smart Cushion Fog Node - Windows Native Setup
echo ==============================================================

:: Check for Python installation
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON_CMD=python
    goto :python_found
)

py --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON_CMD=py
    goto :python_found
)

echo [ERROR] Python is not installed.
pause
exit /b 1

:python_found
echo [INFO] Using Python command: %PYTHON_CMD%

:: Check if .env file exists
if exist .env goto :env_exists
if not exist .env.example goto :env_exists
echo [INFO] Creating .env from .env.example
copy .env.example .env
:env_exists

:: Create Virtual Environment
if exist "venv\Scripts\python.exe" goto :venv_exists
echo [INFO] Creating Python Virtual Environment
%PYTHON_CMD% -m venv venv
:venv_exists

:: Activate Virtual Environment
echo [INFO] Activating Virtual Environment
call venv\Scripts\activate.bat

:: Install Requirements
echo [INFO] Upgrading pip
python -m pip install --upgrade pip >nul

echo [INFO] Installing main dependencies
python -m pip install -r requirements.txt -q

echo [INFO] Installing launcher dependencies
python -m pip install -r launcher\requirements.txt -q

echo [INFO] Setup complete! Starting the application
echo ==============================================================

:: Run the application
python run_launcher.py

pause
