"""
Convenience entry point for the Fog Node Launcher.

Usage:
    python run_launcher.py
"""

import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    # Check for subprocess calls from PyInstaller standalone executable
    if len(sys.argv) > 1 and sys.argv[1] == "--backend":
        # Override sys.argv to make app.py happy
        sys.argv = ["app.py"]
        import app
        sys.exit(0)
        
    elif len(sys.argv) > 1 and sys.argv[1] == "--train":
        # sys.argv: ['run_launcher.py', '--train', 'model_type', 'dataset_path']
        model_type = sys.argv[2]
        dataset_path = sys.argv[3]
        
        # Override sys.argv to match what the training script expects: ['train_*.py', 'dataset_path']
        sys.argv = [f"train_{model_type}.py", dataset_path]
        
        if model_type == "keras":
            import ai.train_v4
        elif model_type == "random_forest":
            import ai.train_rf
        sys.exit(0)

    # Normal execution: Launch the GUI Main Window
    from launcher.main_window import FogLauncherApp
    app = FogLauncherApp()
    app.mainloop()
