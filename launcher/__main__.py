"""
Smart Cushion Fog Node Launcher – Entry Point

Run with:
    python -m launcher
    python run_launcher.py
"""

from launcher.main_window import FogLauncherApp


def main() -> None:
    app = FogLauncherApp()
    app.mainloop()


if __name__ == "__main__":
    main()
