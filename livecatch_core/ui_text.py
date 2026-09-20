"""Windows Unicode helpers and UI-safe text utilities."""
from __future__ import annotations

import ctypes
import os
import sys


# Typical UTF-8-as-CP932 mojibake markers. Keep this source ASCII-only so the
# repair path itself cannot be corrupted by a locale-sensitive build step.
_MOJIBAKE_MARKERS = tuple(
    "\u7e3a\u7e67\u7e5d\u9aad\u9015\u96a7\u908f\u879f\u92e4"
    "\u95be\u870d\u9e78\u9b2e\u95d5\u96c9\u873f"
)


def repair_mojibake(value: str) -> str:
    """Undo the common UTF-8 bytes decoded as CP932 failure when detectable."""
    if not isinstance(value, str) or not value:
        return value
    has_halfwidth = any("\uff61" <= ch <= "\uff9f" for ch in value)
    if not has_halfwidth and not any(marker in value for marker in _MOJIBAKE_MARKERS):
        return value
    try:
        fixed = value.encode("cp932").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    # Only accept a repair that removes the suspicious signature.
    before = sum(value.count(marker) for marker in _MOJIBAKE_MARKERS)
    after = sum(fixed.count(marker) for marker in _MOJIBAKE_MARKERS)
    return fixed if after < before else value


def configure_windows_utf8() -> None:
    """Use UTF-8 for inherited console/worker I/O without requiring chcp."""
    if os.name != "nt":
        return
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def configure_tk_utf8(root) -> None:
    """Prefer UTF-8 for Tcl external encodings while retaining Unicode objects."""
    if os.name != "nt":
        return
    try:
        root.tk.call("encoding", "system", "utf-8")
    except Exception:
        pass


def format_elapsed(seconds: float | int | None) -> str:
    total = max(0, int(seconds or 0))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
