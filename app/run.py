#!/usr/bin/env python3
"""Source CLI entrypoint; native launchers start real_estate_finder.start directly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from real_estate_finder.cli import app  # noqa: E402

if __name__ == "__main__":
    app()
