"""Names and small I/O helpers for the files saved next to a recording.

Everything a recording produces besides the video shares one *base* path
(the video path without its extension), e.g. ``dir/20260101_title``:

=========================  ==============================================
``base.live_chat.json``    YouTube live chat / chat replay (yt-dlp format)
``base.chat.jsonl``        Twitch chat (normalized :class:`chat.ChatEvent`)
``base.stats.jsonl``       viewer/title samples
``base.livecatch.json``    manifest used to align chat with the video
``base.highlights.json``   highlight report (machine readable)
``base.highlights.txt``    highlight report (human readable)
=========================  ==============================================
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

YOUTUBE_CHAT = ".live_chat.json"
TWITCH_CHAT = ".chat.jsonl"
STATS = ".stats.jsonl"
MANIFEST = ".livecatch.json"
HIGHLIGHTS_JSON = ".highlights.json"
HIGHLIGHTS_TXT = ".highlights.txt"
SIDE_SUFFIXES = (YOUTUBE_CHAT, TWITCH_CHAT, STATS, MANIFEST, HIGHLIGHTS_JSON, HIGHLIGHTS_TXT,
                 ".info.json")
VIDEO_SUFFIXES = (".mp4", ".mkv", ".webm", ".m4a", ".ts")


def base_of(path: Path | str) -> Path:
    """``dir/name.mp4`` / ``dir/name.live_chat.json`` → ``dir/name``."""
    path = Path(path)
    for suffix in SIDE_SUFFIXES + VIDEO_SUFFIXES:
        if path.name.endswith(suffix):
            return path.with_name(path.name[:-len(suffix)])
    return path


@dataclass(frozen=True)
class StreamFiles:
    base: Path

    @classmethod
    def for_path(cls, path: Path | str) -> StreamFiles:
        return cls(base_of(path))

    def _with(self, suffix: str) -> Path:
        return self.base.with_name(self.base.name + suffix)

    @property
    def youtube_chat(self) -> Path:
        return self._with(YOUTUBE_CHAT)

    @property
    def twitch_chat(self) -> Path:
        return self._with(TWITCH_CHAT)

    @property
    def stats(self) -> Path:
        return self._with(STATS)

    @property
    def manifest(self) -> Path:
        return self._with(MANIFEST)

    @property
    def highlights_json(self) -> Path:
        return self._with(HIGHLIGHTS_JSON)

    @property
    def highlights_txt(self) -> Path:
        return self._with(HIGHLIGHTS_TXT)

    def chat(self) -> Path | None:
        """The chat file that exists, YouTube first."""
        return next((p for p in (self.youtube_chat, self.twitch_chat) if p.is_file()), None)

    def read_manifest(self) -> dict:
        try:
            data = json.loads(self.manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write_manifest(self, data: dict) -> None:
        write_json(self.manifest, data)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path: Path, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
