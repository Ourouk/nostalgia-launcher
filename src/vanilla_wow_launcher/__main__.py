"""Allow ``python -m vanilla_wow_launcher``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
