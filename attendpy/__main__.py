"""Allow running attendpy as `python -m attendpy`."""

from __future__ import annotations

from attendpy.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
