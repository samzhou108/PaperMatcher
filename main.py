#!/usr/bin/env python3
"""
PaperMatcher - macOS Desktop Application Entry Point
Searches PubMed database, scores articles against research profile, and tracks results.
"""

import json
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


from app.version import __version__
APP_NAME = "PaperMatcher"
CONFIG_DIR = Path.home() / ".papermatcher"
CONFIG_PATH = CONFIG_DIR / "config.json"


def is_first_run() -> bool:
    """Check if this is the first run (no config file exists)."""
    return not CONFIG_PATH.exists()


def _bundle_base() -> Path:
    """Return the base directory for bundled resources (works frozen and from source)."""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def _load_bundled_config():
    """Return a pre-seeded AppConfig if app/bundled_config.json is present, else None."""
    path = _bundle_base() / "app" / "bundled_config.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        config = AppConfig()
        for key, val in data.get("llm", {}).items():
            if hasattr(config.llm, key):
                setattr(config.llm, key, val)
        return config
    except Exception:
        return None


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
        # Launch onboarding wizard, pre-seeded if a bundled config is present
        initial_config = _load_bundled_config()
        wizard = OnboardingWizard(root, on_complete=lambda config: launch_main(root, config),
                                  initial_config=initial_config)
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