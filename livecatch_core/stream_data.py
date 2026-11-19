"""Per-recording stream data: sidecar lifecycle, manifest and highlight detection.

Used by the recording worker. Every failure here is reported as a warning and
never fails the recording itself.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
from threading import Event
import time

from .config import Settings
from .stream_files import StreamFiles


def media_duration(path: str | None, ffprobe: str | None) -> float | None:
    if not path or not ffprobe or not Path(path).is_file():
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60, check=False).stdout.strip()
        return float(out) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def video_start_ms(info: dict, *, from_start: bool, now_ms: int) -> int | None:
    """Estimate the wall-clock time of the first frame of the saved video.

    Finished videos need no estimate (chat replay carries exact offsets). A live
    recording that catches up from the start begins at the broadcast start;
    otherwise it begins roughly now, at the live edge.
    """
    if info.get("is_live") is not True:
        return None
    stamp = info.get("release_timestamp") or info.get("timestamp")
    if from_start and isinstance(stamp, (int, float)) and stamp > 0:
        return int(stamp * 1000)
    return now_ms


def sidecar_target(info: dict, settings: Settings) -> tuple[str, str, str]:
    """Return ``(service, single-broadcast URL, twitch login)`` for the sidecar."""
    from .twitch_watch import normalize_channel

    extractor = str(info.get("extractor_key") or "")
    if not extractor.lower().startswith("twitch"):
        return "youtube", f"https://www.youtube.com/watch?v={info.get('id') or ''}", ""
    try:
        login = normalize_channel(settings.url)
    except ValueError:
        login = str(info.get("uploader_id") or "")
    if info.get("is_live") is True and login:
        return "twitch", f"https://www.twitch.tv/{login}", login
    return "twitch", str(info.get("webpage_url") or settings.url), login


class StreamData:
    """Sidecar (chat/stats/subtitles) and highlight bookkeeping for one recording."""

    def __init__(self, settings: Settings, emit, ffprobe: str | None, factory=None,
                 clock=time.time):
        self.settings, self.emit, self.ffprobe = settings, emit, ffprobe
        self.factory = factory
        self.clock = clock
        self.sidecar = None
        self.files: StreamFiles | None = None
        self.manifest: dict = {}
        self.live = False
        self.output: str | None = None

    @property
    def base(self) -> Path | None:
        return self.files.base if self.files else None

    @property
    def wanted(self) -> bool:
        s = self.settings
        return s.save_live_chat or s.record_stats or s.save_extras or s.detect_highlights

    # --- start ------------------------------------------------------------

    def start(self, info: dict, filename: str, *, from_start: bool) -> None:
        if not self.wanted or self.files is not None:
            return
        self.files = StreamFiles(Path(filename).expanduser().absolute().with_suffix(""))
        self.live = info.get("is_live") is True
        service, url, login = sidecar_target(info, self.settings)
        self._write_initial_manifest(info, service, url, from_start=from_start)
        self._start_sidecar(service, url, login)

    def _write_initial_manifest(self, info: dict, service: str, url: str, *, from_start: bool) -> None:
        now_ms = int(self.clock() * 1000)
        video_id = str(info.get("id") or "")
        start_ms = video_start_ms(info, from_start=from_start, now_ms=now_ms)
        previous = self.files.read_manifest()
        if previous.get("video_id") == video_id and type(previous.get("video_start_epoch_ms")) is int:
            start_ms = previous["video_start_epoch_ms"]  # retry of the same broadcast
        self.manifest = {
            "livecatch_stream_data": 1, "service": service, "video_id": video_id, "url": url,
            "title": str(info.get("title") or "")[:300], "live": self.live,
            "from_start": bool(from_start), "recording_started_epoch_ms": now_ms,
            "video_start_epoch_ms": start_ms,
            "timing_note": ("live chat times are mapped with the estimated video start"
                            if self.live else "chat replay offsets are exact"),
        }
        try:
            self.files.write_manifest(self.manifest)
        except OSError as exc:
            self.emit("warning", message=f"Could not write stream-data manifest: {exc}")

    def _start_sidecar(self, service: str, url: str, login: str) -> None:
        from .sidecar import Job, SidecarProcess, tasks_for

        s = self.settings
        job = Job(service=service, url=url, base=str(self.files.base), live=self.live,
                  chat=s.save_live_chat, stats=s.record_stats, subtitles=s.save_extras,
                  stats_interval=s.stats_interval, twitch_login=login,
                  cookies_browser=s.browser if s.cookies_from_browser else "")
        names = [name for name, _func in tasks_for(job)]
        if not names:
            return
        try:
            self.sidecar = (self.factory or SidecarProcess)(job, self.emit)
            self.emit("log", message="Stream data: " + ", ".join(names))
        except Exception as exc:
            self.emit("warning", message=f"Stream data sidecar failed to start: {type(exc).__name__}: {exc}")

    # --- finish -----------------------------------------------------------

    def finish(self, cancel: Event) -> None:
        if self.files is None:
            return
        self._stop_sidecar(cancel)
        self._finalize_manifest()
        if self.settings.detect_highlights:
            self._detect_highlights()

    def _stop_sidecar(self, cancel: Event) -> None:
        if self.sidecar is None:
            return
        try:
            if self.live or cancel.is_set():
                self.sidecar.close()
            # Chat replay / subtitles of a finished video end on their own.
            elif not self.sidecar.wait(None, cancel=cancel):
                self.sidecar.close()
        except Exception as exc:
            self.emit("warning", message=f"Stream data sidecar: {type(exc).__name__}: {exc}")

    def _finalize_manifest(self) -> None:
        duration = media_duration(self.output, self.ffprobe)
        if duration:
            self.manifest["duration"] = round(duration, 3)
        if self.output:
            self.manifest["video"] = Path(self.output).name
        try:
            self.files.write_manifest(self.manifest)
        except OSError:
            pass

    def _detect_highlights(self) -> None:
        from .highlight_report import analyze_recording

        try:
            result = analyze_recording(self.files.base, sensitivity=self.settings.highlight_sensitivity,
                                       language=self.settings.language)
        except Exception as exc:
            self.emit("warning", message=f"Highlight detection failed: {type(exc).__name__}: {exc}")
            return
        if result is None:
            return
        report, json_path, txt_path = result
        found = report["highlights"]
        top = sorted(found, key=lambda h: h["rank"])[:3]
        summary = " / ".join(f"{h['start_hms']} {h['label_text']}" for h in top)
        message = (f"{len(found)} highlights: {summary} → {txt_path.name}"
                   if self.settings.language == "en"
                   else f"見所 {len(found)}件: {summary} → {txt_path.name}")
        self.emit("highlights", message=message, path=str(txt_path), json=str(json_path),
                  count=len(found))
