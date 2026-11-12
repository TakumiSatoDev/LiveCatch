"""Isolated yt-dlp worker. The first stdin line is settings JSON; later lines
are commands. GUI threads never call Tk through this process boundary.
"""
from __future__ import annotations

from concurrent.futures import CancelledError
from contextlib import nullcontext
import _thread
import json
import re
from pathlib import Path
import subprocess
import sys
import time
from threading import Event, Thread

from .channels import YOUTUBE_ID, validate_recording_url
from .config import Settings, normalize_url
from .events import Emitter
from .options import ydl_options, youtube_recovery_format
from .progress import stream_key, number
from .tools import find_tool
from .ytdlp_patch import fragment_patch, ffmpeg_stop_bridge
from .ui_text import configure_windows_utf8


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


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


class StreamData:
    """Sidecar (chat/stats/subtitles) and highlight bookkeeping for one recording."""

    def __init__(self, settings: Settings, emit, ffprobe: str | None, factory=None):
        self.settings, self.emit, self.ffprobe = settings, emit, ffprobe
        self.factory = factory
        self.sidecar = None
        self.base: Path | None = None
        self.manifest: dict = {}
        self.live = False
        self.output: str | None = None

    @property
    def wanted(self) -> bool:
        s = self.settings
        return s.save_live_chat or s.record_stats or s.save_extras or s.detect_highlights

    def start(self, info: dict, filename: str, *, from_start: bool) -> None:
        from .sidecar import Job, SidecarProcess
        from .twitch_watch import normalize_channel

        if not self.wanted or self.base is not None:
            return
        extractor = str(info.get("extractor_key") or "")
        service = "twitch" if extractor.lower().startswith("twitch") else "youtube"
        self.live = info.get("is_live") is True
        self.base = Path(filename).expanduser().absolute().with_suffix("")
        now_ms = int(time.time() * 1000)
        start_ms = None
        if self.live:
            stamp = info.get("release_timestamp") or info.get("timestamp")
            start_ms = int(stamp * 1000) if from_start and isinstance(stamp, (int, float)) and stamp > 0 \
                else now_ms
        video_id = str(info.get("id") or "")
        manifest_path = Path(str(self.base) + ".livecatch.json")
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        if isinstance(previous, dict) and previous.get("video_id") == video_id \
                and type(previous.get("video_start_epoch_ms")) is int:
            start_ms = previous["video_start_epoch_ms"]  # retry of the same broadcast
        login = ""
        if service == "twitch":
            try:
                login = normalize_channel(self.settings.url)
            except ValueError:
                login = str(info.get("uploader_id") or "")
            url = f"https://www.twitch.tv/{login}" if self.live and login else str(
                info.get("webpage_url") or self.settings.url)
        else:
            url = f"https://www.youtube.com/watch?v={video_id}"
        self.manifest = {
            "livecatch_stream_data": 1, "service": service, "video_id": video_id, "url": url,
            "title": str(info.get("title") or "")[:300], "live": self.live,
            "from_start": bool(from_start), "recording_started_epoch_ms": now_ms,
            "video_start_epoch_ms": start_ms,
            "timing_note": ("live chat times are mapped with the estimated video start"
                            if self.live else "chat replay offsets are exact"),
        }
        try:
            self.base.parent.mkdir(parents=True, exist_ok=True)
            _write_json(manifest_path, self.manifest)
        except OSError as exc:
            self.emit("warning", message=f"Could not write stream-data manifest: {exc}")
        s = self.settings
        job = Job(service=service, url=url, base=str(self.base), live=self.live,
                  chat=s.save_live_chat, stats=s.record_stats, subtitles=s.save_extras,
                  stats_interval=s.stats_interval, twitch_login=login if service == "twitch" else "",
                  cookies_browser=s.browser if s.cookies_from_browser else "")
        if not (job.chat or (job.stats and job.live) or (job.subtitles and not job.live)):
            return
        try:
            self.sidecar = (self.factory or SidecarProcess)(job, self.emit)
            parts = [name for name, on in (("chat", job.chat), ("stats", job.stats and job.live),
                                           ("subtitles", job.subtitles and not job.live)) if on]
            self.emit("log", message="Stream data: " + ", ".join(parts))
        except Exception as exc:
            self.emit("warning", message=f"Stream data sidecar failed to start: {type(exc).__name__}: {exc}")

    def finish(self, cancel: Event) -> None:
        if self.base is None:
            return
        if self.sidecar is not None:
            try:
                if self.live or cancel.is_set():
                    self.sidecar.close()
                else:
                    # Chat replay / subtitles of a finished video end on their own.
                    if not self.sidecar.wait(None, cancel=cancel):
                        self.sidecar.close()
            except Exception as exc:
                self.emit("warning", message=f"Stream data sidecar: {type(exc).__name__}: {exc}")
        duration = media_duration(self.output, self.ffprobe)
        if duration:
            self.manifest["duration"] = round(duration, 3)
        if self.output:
            self.manifest["video"] = Path(self.output).name
        try:
            _write_json(Path(str(self.base) + ".livecatch.json"), self.manifest)
        except OSError:
            pass
        if not self.settings.detect_highlights:
            return
        from .highlights import analyze_recording
        try:
            result = analyze_recording(self.base, sensitivity=self.settings.highlight_sensitivity,
                                       language=self.settings.language)
        except Exception as exc:
            self.emit("warning", message=f"Highlight detection failed: {type(exc).__name__}: {exc}")
            return
        if result is None:
            return
        report, json_path, txt_path = result
        found = report["highlights"]
        top = sorted(found, key=lambda h: h["rank"])[:3]
        ja = self.settings.language != "en"
        summary = " / ".join(f"{h['start_hms']} {h['label_text']}" for h in top)
        message = (f"見所 {len(found)}件: {summary} → {txt_path.name}" if ja
                   else f"{len(found)} highlights: {summary} → {txt_path.name}")
        self.emit("highlights", message=message, path=str(txt_path), json=str(json_path),
                  count=len(found))


def record(settings: Settings, cancel: Event, emit, *, twitch_stream_id: str | None = None,
           youtube_video_id: str | None = None, youtube_recovery: bool = False) -> int:
    import yt_dlp
    from yt_dlp.postprocessor.common import PostProcessor
    from yt_dlp.utils import PostProcessingError

    settings.validate()
    validate_recording_url(settings.url)
    if twitch_stream_id is not None and not re.fullmatch(r"[0-9]{1,32}", twitch_stream_id):
        raise ValueError("Invalid expected Twitch broadcast ID")
    if youtube_video_id is not None and not YOUTUBE_ID.fullmatch(youtube_video_id):
        raise ValueError("Invalid expected YouTube video ID")
    if twitch_stream_id is not None and youtube_video_id is not None:
        raise ValueError("Only one expected broadcast identity is allowed")
    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg AND ffprobe are required in tools/ or PATH")
    for directory in (settings.save_dir, settings.temp_dir if settings.use_temp_dir else settings.save_dir):
        Path(directory).expanduser().mkdir(parents=True, exist_ok=True)

    recovery_abort = Event()
    stream_data = StreamData(settings, emit, ffprobe)

    class Logger:
        def __init__(self):
            self.auth_failures = 0
            self.repeated: dict[str, int] = {}

        @staticmethod
        def _signature(message: str) -> str:
            return re.sub(r"0x[0-9A-Fa-f]+", "0x…", str(message)).strip()

        def _handle(self, kind: str, message) -> None:
            text = str(message or "").strip()
            if not text:
                return
            auth = youtube_video_id is not None and (
                "HTTP Error 401" in text or "HTTP Error 403" in text)
            if auth:
                self.auth_failures += 1
                if self.auth_failures == 1:
                    emit("warning", message=(
                        "YouTube media fragments were rejected (HTTP 401/403). "
                        "LiveCatch will stop this burst instead of retrying every fragment."))
                if self.auth_failures >= 3 and not recovery_abort.is_set():
                    recovery_abort.set()
                    if not youtube_recovery:
                        emit("youtube_recovery", reason="fragment_auth",
                             count=self.auth_failures)
                        emit("warning", message=(
                            "Switching the next retry to YouTube recovery mode: "
                            "fresh live-edge URLs, <=4 fragment workers, combined A/V format preferred."))
                    else:
                        emit("warning", message=(
                            "YouTube recovery mode was also rejected; "
                            "aborting this attempt instead of flooding fragment retries."))
                    cancel.set()
                return

            signature = self._signature(text)
            count = self.repeated.get(signature, 0) + 1
            self.repeated[signature] = count
            if count == 1:
                emit(kind, message=text)
            elif count in (10, 50, 100):
                emit("warning", message=f"Repeated {count}×: {signature[:500]}")

        def debug(self, message):
            if not str(message).startswith("[debug]"):
                self._handle("log", message)
        info = debug
        def warning(self, message):
            self._handle("warning", message)
        def error(self, message):
            self._handle("error", message)

    class ValidateDownload(PostProcessor):
        def run(self, info):
            if cancel.is_set():
                raise KeyboardInterrupt()
            if twitch_stream_id is not None:
                exact_live = (
                    info.get("extractor_key") == "TwitchStream"
                    and info.get("is_live") is True
                    and str(info.get("id", "")) == twitch_stream_id
                )
                associated_vod = (
                    settings.live_from_start
                    and info.get("extractor_key") == "TwitchVod"
                )
                if not (exact_live or associated_vod):
                    raise PostProcessingError(
                        "Twitch broadcast changed/ended after the check; waiting for a fresh check.")
            if youtube_video_id is not None and (
                info.get("extractor_key") != "Youtube"
                or info.get("is_live") is not True
                or info.get("live_status") not in (None, "is_live")
                or str(info.get("id", "")) != youtube_video_id
            ):
                raise PostProcessingError("YouTube broadcast changed/ended after the check; waiting for a fresh check.")
            if settings.mode == "catchup_stop" and info.get("is_live"):
                formats = info.get("requested_formats") or [info]
                if info.get("extractor_key", "").lower() != "youtube" or any(
                    "http_dash_segments_generator" not in f.get("protocol", "") for f in formats
                ):
                    raise PostProcessingError("Snapshot requires YouTube DVR. Twitch live/unknown protocols: use normal recording or a VOD URL.")
            formats = info.get("requested_formats") or [info]
            live = info.get("is_live") is True
            supported = bool(live and settings.engine == "bounded" and all(
                "http_dash_segments_generator" in f.get("protocol", "") for f in formats))
            from_start = bool(live and options.get("live_from_start") and (
                info.get("extractor_key") == "TwitchVod" or all(
                    "http_dash_segments_generator" in f.get("protocol", "") for f in formats)))
            try:
                stream_data.start(info, self._downloader.prepare_filename(info), from_start=from_start)
            except Exception as exc:  # stream data must never fail the recording
                emit("warning", message=f"Stream data disabled: {type(exc).__name__}: {exc}")
            emit("download_context", live=live, streams=len(formats),
                 catchup=bool(supported and options.get("live_from_start")
                              and settings.mode != "catchup_stop"),
                 catchup_supported=supported)
            emit("phase", name="downloading")
            return [], info

    class CaptureOutput(PostProcessor):
        def run(self, info):
            filename = info.get("filepath")
            if filename and Path(filename).is_file():
                stream_data.output = filename
                emit("output", path=filename)
            return [], info

    if twitch_stream_id is not None and settings.live_from_start:
        # yt-dlp may switch a Twitch livestream to its associated growing VOD
        # when live_from_start is requested. Reconfirm the exact live stream ID
        # immediately before allowing that VOD path.
        from .twitch_watch_probe import probe_channel as probe_twitch_channel
        current = probe_twitch_channel(settings)
        if current.get("status") != "live" or str(current.get("stream_id", "")) != twitch_stream_id:
            raise RuntimeError("Twitch broadcast changed/ended before catch-up recording started.")

    options = ydl_options(settings, ffmpeg)
    if youtube_recovery:
        options["live_from_start"] = False
        options["concurrent_fragment_downloads"] = min(
            int(options.get("concurrent_fragment_downloads", 1)), 4)
        options["format"] = youtube_recovery_format(settings)
        options["fragment_retries"] = 1
    if twitch_stream_id is not None or youtube_video_id is not None:
        # Monitoring decides whether to catch up from the available DVR/start or
        # join the live edge. Recovery mode explicitly forces fresh live-edge
        # URLs after repeated YouTube fragment authorization failures.
        options["live_from_start"] = False if youtube_recovery else settings.live_from_start
        options.pop("wait_for_video", None)
    options["logger"] = Logger()
    def progress(p):
        if cancel.is_set() and p.get("status") == "downloading" and (
                settings.engine == "stock" or "fragment_index" not in p):
            raise KeyboardInterrupt()
        info_dict = p.get("info_dict") or {}
        fragment_index = p.get("fragment_index")
        fragment_count = p.get("fragment_count")
        percent = None
        if isinstance(fragment_index, int) and isinstance(fragment_count, int) and fragment_count > 0:
            percent = min(100.0, max(0.0, fragment_index * 100 / fragment_count))
        elif isinstance(p.get("downloaded_bytes"), int) and isinstance(p.get("total_bytes"), int) \
                and p["total_bytes"] > 0:
            percent = min(100.0, max(0.0, p["downloaded_bytes"] * 100 / p["total_bytes"]))
        emit("progress", stream=stream_key(info_dict.get("format_id")),
             status=p.get("status"), downloaded_bytes=p.get("downloaded_bytes"),
             total_bytes=p.get("total_bytes") or p.get("total_bytes_estimate"),
             speed=number(p.get("speed")), fragment_index=fragment_index,
             fragment_count=fragment_count, percent=percent)

    def postprocess(p):
        if p.get("postprocessor") not in ("ValidateDownload", "CaptureOutput"):
            emit("phase", name="postprocessing")
            emit("phase_progress", phase="postprocessing",
                 detail=str(p.get("postprocessor") or ""), percent=None)

    options["progress_hooks"] = [progress]
    options["postprocessor_hooks"] = [postprocess]
    deno = find_tool("deno")
    if deno:
        options["js_runtimes"] = {"deno": {"path": deno}}
    adapter = fragment_patch(
        cancel, prefetch=settings.prefetch,
        snapshot=settings.mode == "catchup_stop",
        catchup=bool(options.get("live_from_start")) and settings.mode != "catchup_stop",
        emit=emit)
    emit("phase", name="extracting")
    try:
        with ffmpeg_stop_bridge(cancel), (adapter if settings.engine == "bounded" else nullcontext()):
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.add_post_processor(ValidateDownload(ydl), when="before_dl")
                ydl.add_post_processor(CaptureOutput(ydl), when="after_move")
                try:
                    code = ydl.download([normalize_url(settings.url)])
                except (KeyboardInterrupt, CancelledError):
                    if recovery_abort.is_set():
                        raise RuntimeError(
                            "YouTube fragment authorization failed; "
                            + ("recovery mode also failed" if youtube_recovery
                               else "recovery retry requested"))
                    raise
    finally:
        stream_data.finish(cancel)
    if recovery_abort.is_set():
        raise RuntimeError(
            "YouTube fragment authorization failed; "
            + ("recovery mode also failed" if youtube_recovery
               else "recovery retry requested"))
    if code:
        return code
    if cancel.is_set():
        return 130
    return 0


def main() -> int:
    # The worker can be launched as a frozen exe or directly from Python.
    # Force its own stdio to UTF-8; do not rely on the GUI process/console codepage.
    configure_windows_utf8()
    if sys.argv[1:] == ["--twitch-probe"]:
        from .twitch_watch_probe import main as probe_main
        return probe_main()
    if sys.argv[1:] == ["--youtube-probe"]:
        from .youtube_watch_probe import main as probe_main
        return probe_main()
    if sys.argv[1:] == ["--sidecar"]:
        from .sidecar import main as sidecar_main
        return sidecar_main()
    emit = Emitter(sys.stdout)
    cancel = Event()
    phase = {"name": "starting"}

    def publish(kind, **data):
        if kind == "phase":
            phase["name"] = data["name"]
        emit(kind, **data)

    try:
        args = sys.argv[1:]
        allowed = ("--twitch-watch-record", "--youtube-watch-record",
                   "--youtube-watch-record-recovery")
        if args and (len(args) != 2 or args[0] not in allowed):
            raise ValueError("Unknown worker arguments")
        expected_stream = args[1] if args else None
        settings = Settings.from_dict(json.loads(sys.stdin.readline()))

        def control():
            for line in sys.stdin:
                try:
                    command = json.loads(line)
                except ValueError:
                    continue
                if command.get("command") == "stop":
                    cancel.set()
                    publish("log", message="Stop requested; draining scheduled fragments / preserving originals.")
                    # Native fragment loops use cooperative cancellation. Waiting/extraction
                    # and the stock backend need Python's interrupt handling instead.
                    if phase["name"] in ("starting", "extracting"):
                        _thread.interrupt_main()
                    return
            cancel.set()  # Parent disappeared; don't continue new native fragments.

        Thread(target=control, daemon=True, name="lc-control").start()
        if args and args[0] in ("--youtube-watch-record", "--youtube-watch-record-recovery"):
            code = record(
                settings, cancel, publish, youtube_video_id=expected_stream,
                youtube_recovery=args[0] == "--youtube-watch-record-recovery")
        else:
            code = record(settings, cancel, publish, twitch_stream_id=expected_stream)
        status = "completed" if code == 0 else "cancelled" if code == 130 else "failed"
    except (KeyboardInterrupt, CancelledError):
        code, status = 130, "cancelled"
    except Exception as exc:
        publish("error", message=f"{type(exc).__name__}: {exc}")
        code, status = 1, "failed"
    publish("done", status=status, code=code)
    return code
