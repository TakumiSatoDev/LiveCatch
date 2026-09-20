"""Executable discovery; no downloads and no shell command interpolation."""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import sys


def app_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def find_tool(name: str) -> str | None:
    filename = name + (".exe" if os.name == "nt" else "")
    for path in (app_dir() / "tools" / filename, app_dir() / filename):
        if path.is_file():
            return str(path)
    return shutil.which(name)
