#!/usr/bin/env python3
"""
PaperPilot - macOS Desktop Application Entry Point
Searches PubMed database, scores articles against research profile, and tracks results.
"""

import os
import sys
import platform
import customtkinter as ctk
from pathlib import Path

# Ensure app package is importable
sys.path.insert(0, str(Path(__file__).parent))

from app.models.config import AppConfig
from app.gui.app_window import AppWindow
from app.gui.onboarding import OnboardingWizard


APP_NAME = "PaperPilot"
CONFIG_DIR = Path.home() / ".paperPilot"
CONFIG_PATH = CONFIG_DIR / "config.json"


def is_first_run() -> bool:
    """Check if this is the first run (no config file exists)."""
    return not CONFIG_PATH.exists()


def setup_config_dir():
    """Create config directory if it doesn't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def main():
    """Main entry point."""
    # macOS appearance
    if platform.system() == "Darwin":
        os.environ["TK_SILENCE_DEPRECATION"] = "1"

    setup_config_dir()

    # Set customtkinter appearance
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title(APP_NAME)
    root.geometry("900x700")
    root.minsize(850, 650)

    # Center window
    root.update_idletasks()
    width = 900
    height = 700
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")

    if is_first_run():
        # Launch onboarding wizard
        wizard = OnboardingWizard(root, on_complete=lambda config: launch_main(root, config))
        wizard.grab_set()
    else:
        # Load existing config and launch main app
        try:
            config = AppConfig.load(CONFIG_PATH)
            launch_main(root, config)
        except Exception as e:
            # Corrupted config, restart onboarding
            OnboardingWizard(root, on_complete=lambda config: launch_main(root, config))

    root.mainloop()


def launch_main(root: ctk.CTk, config: AppConfig):
    """Launch the main application window with loaded config."""
    # Clear any existing widgets (e.g., onboarding)
    for widget in root.winfo_children():
        widget.destroy()

    AppWindow(root, config)


if __name__ == "__main__":
    main()