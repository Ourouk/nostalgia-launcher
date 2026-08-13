"""Octo Updater — entry point.

The application lives in app.py (and the extracted service modules); this
file only wires the config store paths and starts the Tk mainloop, so the
PyInstaller build command (`pyinstaller --onefile ... octo_updater.py`)
keeps working unchanged.
"""

import config_store
from app import OctoUpdaterApp
from constants import CACHE_FILE, CONFIG_FILE

config_store.configure(CONFIG_FILE, CACHE_FILE)


def main():
    app = OctoUpdaterApp()
    app.mainloop()


if __name__ == "__main__":
    main()
