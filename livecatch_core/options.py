"""Pure mapping from application settings to the documented Python API."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from .config import DEFAULT_TEMPLATE, QUALITY, Settings, normalize_url


def _output_template(settings: Settings) -> str:
    template = settings.output_template
    if template != DEFAULT_TEMPLATE:
        return template
    host = (urlsplit(normalize_url(settings.url)).hostname or "").lower()
    service = "Twitch" if host == "twitch.tv" or host.endswith(".twitch.tv") else "YouTube"
    return f"{service}/{template}"




def youtube_recovery_format(settings: Settings) -> str:
    """Prefer one combined YouTube stream during auth-recovery retries.

    Recovery is only used after the normal high-quality/from-start attempt failed
    with repeated 401/403 fragment errors. Respect explicit height caps.
    """
    caps = {
        "catchup_480p30": 480, "480p": 480,
        "catchup_720p30": 720, "720p": 720,
        "1080p": 1080, "recommended_1080p": 1080,
        "1440p": 1440,
    }
    height = caps.get(settings.quality_preset)
    cap = f"[height<={height}]" if height else ""
    # A combined A/V format avoids a second authenticated media URL. If none is
    # available, retain a capped separate-stream fallback instead of failing the
    # selector immediately.
    return f"b{cap}/b/bv*{cap}+ba/b{cap}"

def ydl_options(settings: Settings, ffmpeg: str | None = None) -> dict:
    settings.validate()
    paths = {"home": str(Path(settings.save_dir).expanduser())}
    if settings.use_temp_dir:
        paths["temp"] = str(Path(settings.temp_dir).expanduser())
    processors = [{"key": "FFmpegVideoRemuxer", "preferedformat": settings.output_format}]
    if settings.embed_metadata and not (
        settings.mode == "catchup_stop" and settings.lightweight_catchup_postprocess
    ):
        processors.append({"key": "FFmpegMetadata", "add_metadata": True})
    result = {
        "format": QUALITY[settings.quality_preset],
        "paths": paths,
        "outtmpl": _output_template(settings),
        "merge_output_format": settings.output_format,
        "postprocessors": processors,
        "concurrent_fragment_downloads": settings.concurrent_fragments,
        "live_from_start": settings.live_from_start or settings.mode != "reservation",
        "writeinfojson": settings.write_info_json,
        "noplaylist": True,
        "continuedl": True,
        "overwrites": False,
        "skip_unavailable_fragments": False,
        "retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 3,
        "socket_timeout": 10,
        "quiet": True,
        "noprogress": True,
        "windowsfilenames": os.name == "nt",
        "retry_sleep_functions": {
            "http": lambda n: min(2 ** n, 30),
            "fragment": lambda n: min(2 ** n, 30),
        },
    }
    if settings.mode == "reservation":
        result["wait_for_video"] = (settings.wait_seconds, settings.wait_seconds)
    if settings.cookies_from_browser:
        result["cookiesfrombrowser"] = (settings.browser,)
    if ffmpeg:
        result["ffmpeg_location"] = str(Path(ffmpeg).parent)
    return result
