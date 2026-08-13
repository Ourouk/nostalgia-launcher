"""Allow ``python -m octo_updater``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
