"""Small, optional GitHub-backed update check for the GUI."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shutil
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen
import zipfile

REPOSITORY_URL = "https://github.com/TakumiSatoDev/LiveCatch/releases/latest"
RELEASE_API_URL = "https://api.github.com/repos/TakumiSatoDev/LiveCatch/releases/latest"
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
    download_url: str | None = None


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


def _fetch_main_version(timeout: float) -> str:
    request = Request(
        MAIN_VERSION_URL,
        headers={"User-Agent": "LiveCatch-update-check", "Accept": "text/plain"},
    )
    with urlopen(request, timeout=timeout) as response:
        source = response.read(64 * 1024).decode("utf-8", errors="replace")
    match = _VERSION_RE.search(source)
    if not match:
        raise ValueError("Remote LiveCatch version was not found")
    return match.group("version").strip()


def _fetch_latest_release(timeout: float) -> tuple[str, str, str]:
    request = Request(
        RELEASE_API_URL,
        headers={"User-Agent": "LiveCatch-update-check", "Accept": "application/vnd.github+json"},
    )
    with urlopen(request, timeout=timeout) as response:
        release = json.loads(response.read(256 * 1024).decode("utf-8", errors="replace"))
    latest = str(release.get("tag_name", "")).strip().removeprefix("v")
    if _version_key(latest) is None:
        raise ValueError(f"Invalid release version: {latest}")
    release_url = str(release.get("html_url") or REPOSITORY_URL)
    assets = release.get("assets") or []
    asset = next((item for item in assets
                  if str(item.get("name", "")).startswith("LiveCatch-Update-")
                  and str(item.get("name", "")).endswith(".zip")), None)
    if not asset or not asset.get("browser_download_url"):
        raise ValueError("Latest release has no LiveCatch update package")
    return latest, release_url, str(asset["browser_download_url"])


def check_for_update(current_version: str, *, timeout: float = 3) -> UpdateCheck:
    try:
        latest, release_url, download_url = _fetch_latest_release(timeout)
    except Exception:
        # Keep the check useful for development builds or temporary API failures.
        latest = _fetch_main_version(timeout)
        if _version_key(latest) is None:
            raise ValueError(f"Invalid remote LiveCatch version: {latest}")
        return UpdateCheck(current_version, latest, is_newer(current_version, latest))
    return UpdateCheck(current_version, latest, is_newer(current_version, latest), release_url, download_url)


def download_update(update: UpdateCheck, *, timeout: float = 30) -> Path:
    """Download and validate the self-update payload, returning its staging directory."""
    if not update.download_url:
        raise ValueError("No self-update package is available")
    root = Path(tempfile.mkdtemp(prefix="livecatch-update-"))
    archive = root / "update.zip"
    partial = root / "update.zip.part"
    try:
        request = Request(
            update.download_url,
            headers={"User-Agent": "LiveCatch-update-downloader", "Accept": "application/octet-stream"},
        )
        with urlopen(request, timeout=timeout) as response, partial.open("wb") as output:
            size = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise ValueError("Update package is unexpectedly large")
                output.write(chunk)
        if size == 0:
            raise ValueError("Update package is empty")
        partial.replace(archive)

        payload = root / "payload"
        with zipfile.ZipFile(archive) as package:
            names = package.namelist()
            if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
                raise ValueError("Update package contains an unsafe path")
            required = {"LiveCatch.exe", "LiveCatchWorker.exe", "LiveCatchUpdater.exe"}
            if not required.issubset(names):
                raise ValueError("Update package is missing required application files")
            package.extractall(payload)
        return payload
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
