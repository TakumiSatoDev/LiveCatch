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
import sys
from threading import Event, Thread

from .config import Settings, normalize_url
from .events import Emitter
from .media import ExportOptions, export_batch
from .options import ydl_options
from .tools import find_tool
from .ytdlp_patch import fragment_patch, ffmpeg_stop_bridge


def record(settings: Settings, cancel: Event, emit, *, twitch_stream_id: str | None = None) -> int:
    import yt_dlp
    from yt_dlp.postprocessor.common import PostProcessor
    from yt_dlp.utils import PostProcessingError

    settings.validate()
    if twitch_stream_id is not None and not re.fullmatch(r"[0-9]{1,32}", twitch_stream_id):
        raise ValueError("Invalid expected Twitch broadcast ID")
    ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg AND ffprobe are required in tools/ or PATH")
    for directory in (settings.save_dir, settings.temp_dir if settings.use_temp_dir else settings.save_dir):
        Path(directory).expanduser().mkdir(parents=True, exist_ok=True)
    outputs = []

    class Logger:
        def debug(self, message):
            if not message.startswith("[debug]"):
                emit("log", message=message)
        info = debug
        def warning(self, message):
            emit("warning", message=message)
        def error(self, message):
            emit("error", message=message)

    class ValidateDownload(PostProcessor):
        def run(self, info):
            if cancel.is_set():
                raise KeyboardInterrupt()
            if twitch_stream_id is not None and (
                info.get("extractor_key") != "TwitchStream"
                or info.get("is_live") is not True
                or str(info.get("id", "")) != twitch_stream_id
            ):
                raise PostProcessingError("Twitch broadcast changed/ended after the check; waiting for a fresh check.")
            if settings.mode == "catchup_stop" and info.get("is_live"):
                formats = info.get("requested_formats") or [info]
                if info.get("extractor_key", "").lower() != "youtube" or any(
                    "http_dash_segments_generator" not in f.get("protocol", "") for f in formats
                ):
                    raise PostProcessingError("Snapshot requires YouTube DVR. Twitch live/unknown protocols: use normal recording or a VOD URL.")
            emit("phase", name="downloading")
            return [], info

    class CaptureOutput(PostProcessor):
        def run(self, info):
            filename = info.get("filepath")
            if filename and Path(filename).is_file():
                outputs.append(Path(filename))
                emit("output", path=filename)
            return [], info

    options = ydl_options(settings, ffmpeg)
    if twitch_stream_id is not None:
        options["live_from_start"] = False  # Record live HLS, never an associated growing VOD.
        options.pop("wait_for_video", None)  # Do not wait for a DIFFERENT broadcast after a race.
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
        emit("progress", stream=info_dict.get("format_id", "media"),
             status=p.get("status"), downloaded_bytes=p.get("downloaded_bytes"),
             total_bytes=p.get("total_bytes") or p.get("total_bytes_estimate"),
             speed=p.get("speed"), fragment_index=fragment_index,
             fragment_count=fragment_count, percent=percent)

    def postprocess(p):
        if p.get("postprocessor") not in ("ValidateDownload", "CaptureOutput"):
            emit("phase", name="postprocessing")

    options["progress_hooks"] = [progress]
    options["postprocessor_hooks"] = [postprocess]
    deno = find_tool("deno")
    if deno:
        options["js_runtimes"] = {"deno": {"path": deno}}
    adapter = fragment_patch(cancel, prefetch=settings.prefetch,
                             snapshot=settings.mode == "catchup_stop", emit=emit)
    emit("phase", name="extracting")
    with ffmpeg_stop_bridge(cancel), (adapter if settings.engine == "bounded" else nullcontext()):
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.add_post_processor(ValidateDownload(ydl), when="before_dl")
            ydl.add_post_processor(CaptureOutput(ydl), when="after_move")
            code = ydl.download([normalize_url(settings.url)])
    if code:
        return code
    if cancel.is_set():
        return 130
    if settings.gpu_export != "off":
        emit("phase", name="exporting")
        export_batch(outputs, ExportOptions(settings.gpu_export, settings.export_height,
                                            settings.gpu_device, settings.gpu_jobs,
                                            settings.gpu_preset),
                     ffmpeg, ffprobe, cancel, emit)
    return 0


def main() -> int:
    if sys.argv[1:] == ["--twitch-probe"]:
        from .twitch_watch_probe import main as probe_main
        return probe_main()
    emit = Emitter(sys.stdout)
    cancel = Event()
    phase = {"name": "starting"}

    def publish(kind, **data):
        if kind == "phase":
            phase["name"] = data["name"]
        emit(kind, **data)

    try:
        args = sys.argv[1:]
        if args and (len(args) != 2 or args[0] != "--twitch-watch-record"):
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
        code = record(settings, cancel, publish, twitch_stream_id=expected_stream)
        status = "completed" if code == 0 else "cancelled" if code == 130 else "failed"
    except (KeyboardInterrupt, CancelledError):
        code, status = 130, "cancelled"
    except Exception as exc:
        publish("error", message=f"{type(exc).__name__}: {exc}")
        code, status = 1, "failed"
    publish("done", status=status, code=code)
    return code
