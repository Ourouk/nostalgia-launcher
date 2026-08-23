"""PyInstaller entry shim.

The specs freeze the whole ``nostalgia_launcher`` package, so this top-level script
imports the real entry point absolutely (relative imports inside the package
would fail if cli.py itself were the frozen script).
"""

from nostalgia_launcher.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
