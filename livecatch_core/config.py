"""Validated settings and atomic, backwards-compatible configuration storage."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

CONFIG_FILE = Path.home() / ".livecatch_config.json"
LEGACY_DEFAULT_TEMPLATE_V3 = "%(extractor_key)s/%(upload_date)s_%(channel)s_%(title)s/%(upload_date)s_%(title)s.%(ext)s"
LEGACY_DEFAULT_TEMPLATE_V2 = LEGACY_DEFAULT_TEMPLATE_V3.removeprefix("%(extractor_key)s/")
DEFAULT_TEMPLATE = "%(uploader_id)s/%(upload_date)s_%(id)s_%(title)s/%(upload_date)s_%(title)s.%(ext)s"
QUALITY = {
    "recommended_1080p": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "catchup_720p30": "bv*[height<=720][fps<=30]+ba/b[height<=720][fps<=30]/bv*[height<=720]+ba/b[height<=720]/b",
    "catchup_480p30": "bv*[height<=480][fps<=30]+ba/b[height<=480][fps<=30]/bv*[height<=480]+ba/b[height<=480]/b",
    "best": "bv*+ba/b",
    **{f"{h}p": f"bv*[height<={h}]+ba/b[height<={h}]/b" for h in (1440, 1080, 720, 480, 360)},
}
BROWSERS = ("chrome", "edge", "firefox", "brave", "vivaldi", "opera")


def normalize_url(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        raise ValueError("Only HTTP(S) YouTube/Twitch URLs without credentials are accepted.")
    host = (parsed.hostname or "").lower()
    roots = ("youtube.com", "youtu.be", "youtube-nocookie.com", "twitch.tv")
    if not any(host == root or host.endswith("." + root) for root in roots):
        raise ValueError("YouTube / Twitch のURLを指定してください。")
    if parsed.port not in (None, 80, 443):
        raise ValueError("Unexpected URL port.")
    return value


@dataclass(frozen=True)
class Settings:
    schema_version: int = 3
    language: str = "ja"
    mode: str = "reservation"
    url: str = ""
    save_dir: str = str(Path.home() / "Videos" / "LiveCatch")
    use_temp_dir: bool = False
    temp_dir: str = str(Path.home() / "Videos" / "LiveCatch_temp")
    wait_seconds: int = 30
    cookies_from_browser: bool = False
    browser: str = "chrome"
    live_from_start: bool = True
    write_info_json: bool = True
    embed_metadata: bool = True
    lightweight_catchup_postprocess: bool = False
    quality_preset: str = "recommended_1080p"
    output_format: str = "mp4"
    concurrent_fragments: int = 8
    output_template: str = DEFAULT_TEMPLATE
    engine: str = "bounded"
    prefetch: int = 2
    gpu_export: str = "off"
    gpu_device: int = 0
    export_height: int = 0
    gpu_jobs: int = 1
    gpu_preset: str = "balanced"

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        if not isinstance(data, dict):
            raise ValueError("Configuration must be a JSON object.")
        defaults = cls()
        clean = {}
        for f in fields(cls):
            value = data.get(f.name, getattr(defaults, f.name))
            default = getattr(defaults, f.name)
            if type(value) is not type(default):
                raise ValueError(f"Invalid type for {f.name}")
            clean[f.name] = value
        clean["schema_version"] = 3
        # Automatic recording/export in the desktop app is now always stream-copy only.
        # Keep legacy fields readable for backwards compatibility and the standalone export CLI.
        clean["gpu_export"] = "off"
        if clean["output_template"] in (LEGACY_DEFAULT_TEMPLATE_V2, LEGACY_DEFAULT_TEMPLATE_V3):
            clean["output_template"] = DEFAULT_TEMPLATE
        result = cls(**clean)
        result.validate(require_url=False)
        return result

    def validate(self, *, require_url: bool = True) -> None:
        choices = {
            "language": ("ja", "en"),
            "mode": ("reservation", "live_full", "catchup_stop"),
            "browser": BROWSERS,
            "quality_preset": QUALITY,
            "output_format": ("mp4", "mkv", "webm"),
            "engine": ("bounded", "stock"),
            "gpu_export": ("off", "auto", "cuda", "cpu"),
            "gpu_preset": ("balanced", "fast", "max_speed"),
        }
        for name, allowed in choices.items():
            if getattr(self, name) not in allowed:
                raise ValueError(f"Invalid {name}: {getattr(self, name)!r}")
        for name, lo, hi in (("wait_seconds", 5, 3600), ("concurrent_fragments", 1, 256),
                             ("prefetch", 1, 8), ("gpu_device", 0, 31), ("gpu_jobs", 1, 8)):
            if type(getattr(self, name)) is not int or not lo <= getattr(self, name) <= hi:
                raise ValueError(f"{name} must be in {lo}..{hi}")
        if self.export_height not in (0, 360, 480, 720, 1080, 1440, 2160):
            raise ValueError("Unsupported export height.")
        if not self.save_dir.strip() or (self.use_temp_dir and not self.temp_dir.strip()):
            raise ValueError("保存先・一時保存先を指定してください。")
        if not self.output_template.strip():
            raise ValueError("Output template cannot be empty.")
        if self.mode == "catchup_stop" and self.engine != "bounded":
            raise ValueError("Snapshot mode requires the bounded engine.")
        if require_url:
            normalize_url(self.url)

    def to_dict(self) -> dict:
        return asdict(self)


class ConfigStore:
    def __init__(self, path: Path = CONFIG_FILE):
        self.path = path

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        # The legacy GUI allowed dangerous 64/128-way bursts. Migrate conservatively.
        if isinstance(data, dict):
            schema = data.get("schema_version", 2)
            if type(schema) is not int:
                raise ValueError("Invalid configuration schema_version")
            if schema < 3:
                workers = data.get("concurrent_fragments", 8)
                if type(workers) is not int:
                    raise ValueError("Invalid legacy concurrent_fragments")
                data["concurrent_fragments"] = min(workers, 32)
        return Settings.from_dict(data)

    def save(self, settings: Settings) -> None:
        settings.validate(require_url=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Preserve unknown legacy keys; never overwrite a corrupt file silently.
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError("Invalid existing configuration; back it up before replacing it.")
        data.update(settings.to_dict())
        fd, name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(data, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
