"""Small, optional GitHub-backed update check for the GUI."""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.request import Request, urlopen

REPOSITORY_URL = "https://github.com/TakumiSatoDev/LiveCatch/releases/latest"
MAIN_VERSION_URL = (
    "https://raw.githubusercontent.com/TakumiSatoDev/LiveCatch/main/"
    "livecatch_core/__init__.py"
)
_VERSION_RE = re.compile(
    r"__version__\s*=\s*[\"'](?P<version>[^\"']+)[\"']"
)
_VERSION_PARTS = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-.]([0-9A-Za-z]+))?$")


@dataclass(frozen=True)
class UpdateCheck:
    current_version: str
    latest_version: str
    update_available: bool
    url: str = REPOSITORY_URL


def _version_key(value: str) -> tuple | None:
    match = _VERSION_PARTS.fullmatch(value.strip())
    if not match:
        return None
    major, minor, patch = (int(match.group(i)) for i in range(1, 4))
    suffix = match.group(4)
    if suffix is None:
        return major, minor, patch, 1, 0, ""
    dev = re.fullmatch(r"dev(\d+)", suffix, re.IGNORECASE)
    if dev:
        return major, minor, patch, 0, int(dev.group(1)), "dev"
    return major, minor, patch, 0, 0, suffix.lower()


def is_newer(current_version: str, latest_version: str) -> bool:
    current = _version_key(current_version)
    latest = _version_key(latest_version)
    return current is not None and latest is not None and latest > current


def check_for_update(current_version: str, *, timeout: float = 3) -> UpdateCheck:
    request = Request(
        MAIN_VERSION_URL,
        headers={"User-Agent": "LiveCatch-update-check", "Accept": "text/plain"},
    )
    with urlopen(request, timeout=timeout) as response:
        source = response.read(64 * 1024).decode("utf-8", errors="replace")
    match = _VERSION_RE.search(source)
    if not match:
        raise ValueError("Remote LiveCatch version was not found")
    latest = match.group("version").strip()
    if _version_key(latest) is None:
        raise ValueError(f"Invalid remote LiveCatch version: {latest}")
    return UpdateCheck(current_version, latest, is_newer(current_version, latest))
