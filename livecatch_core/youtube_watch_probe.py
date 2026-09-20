"""Bounded metadata-only YouTube /live resolution in a disposable worker."""
from __future__ import annotations

from itertools import islice
import json
import sys

from .channels import YOUTUBE_ID, target_url, youtube_target
from .config import Settings
from .events import Emitter
from .tools import find_tool
from .twitch_watch_probe import QuietLogger, _is_offline


def resolve_live(ydl, info: dict, *, depth: int = 0) -> dict:
    """Follow only validated YouTube video identities, never arbitrary redirects."""
    if depth > 2 or not isinstance(info, dict):
        raise RuntimeError("YouTube returned an unsupported live response")
    kind = info.get("_type", "video")
    if kind in {"playlist", "multi_video"}:
        # /live is occasionally an actual tab. Do not scan a channel's entire archive.
        entries = info.get("entries")
        if entries is None:
            raise RuntimeError("YouTube live tab has no entries field")
        upcoming = False
        unknown = False
        for entry in islice(entries, 10):
            if not isinstance(entry, dict):
                unknown = True
                continue
            live = entry.get("live_status")
            if live == "is_live" or entry.get("is_live") is True:
                result = resolve_live(ydl, entry, depth=depth + 1)
                if result["status"] == "live":
                    return result
            elif live == "is_upcoming":
                upcoming = True
            elif live not in {"not_live", "was_live", "post_live"}:
                unknown = True
        if unknown:
            raise RuntimeError("YouTube live tab state is unknown; not treated as offline")
        return {"status": "upcoming" if upcoming else "offline"}
    if kind in {"url", "url_transparent"}:
        video_id = info.get("id", "")
        if info.get("ie_key") != "Youtube" or not isinstance(video_id, str) or not YOUTUBE_ID.fullmatch(video_id):
            raise RuntimeError("YouTube live redirect has no validated video ID")
        return resolve_live(ydl, ydl.extract_info(
            f"https://www.youtube.com/watch?v={video_id}", download=False, process=False), depth=depth + 1)
    if kind != "video" or info.get("extractor_key") != "Youtube":
        raise RuntimeError("YouTube video metadata is missing")
    status = info.get("live_status")
    if status == "is_upcoming":
        return {"status": "upcoming"}
    if status in {"not_live", "was_live", "post_live"}:
        return {"status": "offline"}
    if status != "is_live" and info.get("is_live") is not True:
        raise RuntimeError("YouTube live state is unknown; not treated as offline")
    video_id = info.get("id", "")
    if not isinstance(video_id, str) or not YOUTUBE_ID.fullmatch(video_id):
        raise RuntimeError("Invalid YouTube broadcast ID")
    return {"status": "live", "stream_id": video_id, "title": str(info.get("title") or video_id)[:300]}


def probe_channel(settings: Settings) -> dict:
    import yt_dlp
    from yt_dlp.utils import UserNotLive

    url = target_url(youtube_target(settings.url))
    options = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "live_from_start": False, "socket_timeout": 8, "retries": 0,
        "extractor_retries": 0, "cachedir": False, "logger": QuietLogger(),
        "ignore_no_formats_error": True, "playlistend": 10,
    }
    if settings.cookies_from_browser:
        options["cookiesfrombrowser"] = (settings.browser,)
    deno = find_tool("deno")
    if deno:
        options["js_runtimes"] = {"deno": {"path": deno}}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            return resolve_live(ydl, ydl.extract_info(url, download=False, process=False))
    except Exception as exc:
        if _is_offline(exc, UserNotLive):
            return {"status": "offline"}
        raise


def main() -> int:
    emit = Emitter(sys.stdout)
    try:
        settings = Settings.from_dict(json.loads(sys.stdin.readline()))
        emit("probe_result", **probe_channel(settings))
        emit("done", status="completed", code=0)
        return 0
    except Exception as exc:
        emit("error", message=f"{type(exc).__name__}: {exc}")
        emit("done", status="failed", code=1)
        return 1
