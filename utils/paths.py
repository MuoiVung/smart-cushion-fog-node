import sys
import os
import shutil
from pathlib import Path

# Detect PyInstaller frozen state
IS_FROZEN = getattr(sys, 'frozen', False)

if IS_FROZEN:
    # In PyInstaller, sys._MEIPASS holds the temporary extracted directory path
    PROJECT_ROOT = Path(sys._MEIPASS)
    DATA_ROOT = Path.home() / "SmartCushionFog"
else:
    PROJECT_ROOT = Path(__file__).parent.parent.resolve()
    DATA_ROOT = PROJECT_ROOT

def get_db_path() -> Path:
    """Gets the path to the local SQLite database."""
    return DATA_ROOT / "data" / "fog_local.db"

def get_export_dir() -> Path:
    """Gets the path to the directory where collected Excel sheets are saved."""
    return DATA_ROOT / "data_exports"

def get_env_path() -> Path:
    """Gets the path to the user's .env file."""
    return DATA_ROOT / ".env"

def get_labels_file() -> Path:
    """Gets the path to the saved_labels.json settings file."""
    return DATA_ROOT / "launcher" / "saved_labels.json"

def get_models_dir() -> Path:
    """Gets the path to the models directory."""
    return DATA_ROOT / "ai" / "models"

def resolve_model_path(path_str: str) -> str:
    """
    Resolves an AI model or scaler path.
    1. If absolute, return as-is.
    2. If relative, check first in persistent DATA_ROOT (user's home).
    3. If not found in DATA_ROOT, check in read-only PROJECT_ROOT (bundled app resources).
    4. Fall back to path_str otherwise.
    """
    if not path_str:
        return path_str
        
    p = Path(path_str)
    if p.is_absolute():
        return str(p)
        
    # Check user home persistent directory first
    data_p = DATA_ROOT / p
    if data_p.exists():
        return str(data_p)
        
    # Fall back to read-only bundled resources inside app package
    proj_p = PROJECT_ROOT / p
    if proj_p.exists():
        return str(proj_p)
        
    return path_str

def ensure_directories():
    """Initializes persistent directories and copies bundled defaults if missing."""
    if IS_FROZEN:
        # Create user folders
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        (DATA_ROOT / "data").mkdir(parents=True, exist_ok=True)
        (DATA_ROOT / "data_exports").mkdir(parents=True, exist_ok=True)
        (DATA_ROOT / "ai" / "models").mkdir(parents=True, exist_ok=True)
        (DATA_ROOT / "launcher").mkdir(parents=True, exist_ok=True)
        
        # Copy default .env if missing so user has a template
        env_file = DATA_ROOT / ".env"
        if not env_file.exists():
            default_env = PROJECT_ROOT / ".env.example"
            if default_env.exists():
                shutil.copy(default_env, env_file)
            else:
                with open(env_file, "w", encoding="utf-8") as f:
                    f.write("# Smart Cushion Fog Node - Configuration\n")
                    
        # Copy saved_labels.json if missing
        labels_file = DATA_ROOT / "launcher" / "saved_labels.json"
        if not labels_file.exists():
            default_labels = PROJECT_ROOT / "launcher" / "saved_labels.json"
            if default_labels.exists():
                shutil.copy(default_labels, labels_file)
