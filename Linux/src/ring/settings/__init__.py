"""The settings window: edit the configuration without touching the file.

Everything on screen is read from and written back to a `Config`. Applying
saves it as TOML and asks the running service to restart with it.
"""

from ring.settings.window import SettingsWindow, run_settings

__all__ = ["SettingsWindow", "run_settings"]
