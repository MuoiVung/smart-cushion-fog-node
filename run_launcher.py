"""
Convenience entry point for the Fog Node Launcher.

Usage:
    python run_launcher.py
"""

import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

from launcher.main_window import FogLauncherApp

if __name__ == "__main__":
    app = FogLauncherApp()
    app.mainloop()
