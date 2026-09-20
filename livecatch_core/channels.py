"""Validated identities for the mixed watch list; no network or GUI imports."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qs, quote, unquote, urlsplit

YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


def youtube_target(value: str) -> str:
    """Return youtube:@handle or youtube:channel/UC... (IDs stay case-sensitive)."""
    if not isinstance(value, str):
        raise ValueError("Use a YouTube channel URL or @handle")
    value = value.strip()
    if value.startswith("youtube:"):
        path = value[len("youtube:"):]
    elif value.startswith("@"):
        path = value
    elif CHANNEL_ID.fullmatch(value):
        path = "channel/" + value
    else:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if (parsed.scheme not in ("http", "https") or parsed.hostname not in YOUTUBE_HOSTS
                or parsed.username or parsed.password or parsed.port not in (None, 80, 443)
                or any(k in parse_qs(parsed.query) for k in ("v", "list"))):
            raise ValueError("Use a YouTube channel URL, not a video, playlist or another site")
        path = parsed.path.strip("/")
    path = unicodedata.normalize("NFC", unquote(path, errors="strict"))
    parts = path.strip("/").split("/")
    if parts[-1] in {"live", "streams", "videos", "featured"} and len(parts) > 1:
        parts.pop()
    if len(parts) == 1 and parts[0].startswith("@"):
        handle = parts[0][1:]
        if (not 1 <= len(handle) <= 64 or not any(c.isalnum() for c in handle)
                or any(not (c.isalnum() or c in "_.-·") for c in handle)):
            raise ValueError("Invalid YouTube @handle")
        return "youtube:@" + handle.casefold()
    if len(parts) == 2 and parts[0] == "channel" and CHANNEL_ID.fullmatch(parts[1]):
        return "youtube:channel/" + parts[1]
    if len(parts) == 2 and parts[0] in {"user", "c"} and re.fullmatch(r"[\w.-]{1,100}", parts[1]):
        if parts[1] not in {".", ".."}:
            return "youtube:" + "/".join(parts)
    raise ValueError("Use /@handle, /channel/UC..., /user/name or /c/name")


def is_youtube(login: str) -> bool:
    return login.startswith("youtube:")


def normalize_target(value: str) -> str:
    # The legacy function remains Twitch-only for old callers and saved data.
    from .twitch_watch import normalize_channel
    if not isinstance(value, str):
        raise ValueError("Channel must be a name or URL")
    value = value.strip()
    if value.startswith(("youtube:", "@")) or CHANNEL_ID.fullmatch(value):
        return youtube_target(value)
    parsed = urlsplit(value if "://" in value else "https://" + value)
    if parsed.hostname in YOUTUBE_HOSTS:
        return youtube_target(value)
    if value.startswith("twitch:"):
        value = value[len("twitch:"):]
    return normalize_channel(value)


def parse_targets(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(normalize_target(x) for x in re.split(r"[\s,;、]+", text.strip()) if x))


def target_url(login: str) -> str:
    login = normalize_target(login)
    if is_youtube(login):
        return "https://www.youtube.com/" + quote(login[len("youtube:"):], safe="/@-_.") + "/live"
    return f"https://www.twitch.tv/{login}"


def valid_broadcast_id(login: str, stream_id: str) -> bool:
    pattern = YOUTUBE_ID if is_youtube(login) else re.compile(r"[0-9]{1,32}\Z")
    return isinstance(stream_id, str) and pattern.fullmatch(stream_id) is not None


def broadcast_key(login: str, stream_id: str) -> str:
    return ("youtube:video/" if is_youtube(login) else "twitch:stream/") + stream_id


def manual_target(value: str) -> str | None:
    """Recognize channel URLs and YouTube video URLs for manual/auto exclusion."""
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if (parsed.scheme not in ("http", "https") or parsed.username or parsed.password
                or parsed.port not in (None, 80, 443)):
            return None
        video_id = None
        if parsed.hostname in {"youtu.be", "www.youtu.be"}:
            video_id = parsed.path.strip("/")
        elif parsed.hostname in YOUTUBE_HOSTS:
            if parsed.path == "/watch":
                video_id = parse_qs(parsed.query).get("v", [""])[0]
            elif parsed.path.startswith(("/live/", "/shorts/")):
                video_id = parsed.path.strip("/").split("/")[-1]
        if video_id is not None:
            return broadcast_key("youtube:", video_id) if YOUTUBE_ID.fullmatch(video_id) else None
        return normalize_target(value)
    except (ValueError, UnicodeError):
        return None
