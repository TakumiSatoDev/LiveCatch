"""Metadata-only Twitch probe executed by LiveCatchWorker, never by the GUI."""
from __future__ import annotations

import json
import re
import sys

from .config import Settings
from .events import Emitter
from .twitch_watch import normalize_channel


class QuietLogger:
    def debug(self, *_): pass
    def info(self, *_): pass
    def warning(self, *_): pass
    def error(self, *_): pass


def _is_offline(exc: BaseException, offline_type: type) -> bool:
    """Recognize yt-dlp's typed error, not localized message substrings."""
    seen = set()
    pending = [exc]
    while pending and len(seen) < 20:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, offline_type):
            return True
        info = getattr(item, "exc_info", None)
        if isinstance(info, tuple) and len(info) >= 2 and isinstance(info[1], BaseException):
            pending.append(info[1])
        for cause in (getattr(item, "__cause__", None), getattr(item, "__context__", None)):
            if isinstance(cause, BaseException):
                pending.append(cause)
    return False


def probe_channel(settings: Settings) -> dict:
    import yt_dlp
    from yt_dlp.utils import UserNotLive

    login = normalize_channel(settings.url)
    options = {
        "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
        "live_from_start": False, "socket_timeout": 8, "retries": 0,
        "extractor_retries": 0, "cachedir": False, "logger": QuietLogger(),
    }
    if settings.cookies_from_browser:
        options["cookiesfrombrowser"] = (settings.browser,)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"https://www.twitch.tv/{login}", download=False, process=False)
    except Exception as exc:
        if _is_offline(exc, UserNotLive):
            return {"status": "offline"}
        raise
    if not isinstance(info, dict) or info.get("is_live") is not True:
        raise RuntimeError("Twitch returned no confirmed live broadcast; check was not treated as offline")
    stream_id = str(info.get("id", ""))
    if info.get("extractor_key") != "TwitchStream" or not re.fullmatch(r"[0-9]{1,32}", stream_id):
        raise RuntimeError("Twitch broadcast identity is missing or unsupported")
    # Never forward signed playlist URLs, headers, cookies or the full info_dict.
    return {"status": "live", "stream_id": stream_id,
            "title": str(info.get("description") or info.get("title") or login)[:300]}


def main() -> int:
    emit = Emitter(sys.stdout)
    try:
        settings = Settings.from_dict(json.loads(sys.stdin.readline()))
        result = probe_channel(settings)
        emit("probe_result", **result)
        emit("done", status="completed", code=0)
        return 0
    except Exception as exc:
        emit("error", message=f"{type(exc).__name__}: {exc}")
        emit("done", status="failed", code=1)
        return 1
