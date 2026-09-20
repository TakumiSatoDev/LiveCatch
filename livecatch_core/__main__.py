"""Headless recording, GPU diagnosis and parallel media export."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from threading import Event

from .config import Settings
from .events import Emitter
from .media import ExportOptions, export_batch, probe_cuda
from .tools import find_tool


def main():
    parser = argparse.ArgumentParser(prog="python -m livecatch_core")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("gui")
    diagnose = sub.add_parser("diagnose")
    diagnose.add_argument("--device", type=int, default=0)
    record = sub.add_parser("record")
    record.add_argument("config", type=Path)
    export = sub.add_parser("export")
    export.add_argument("files", nargs="+", type=Path)
    export.add_argument("--mode", choices=("cuda", "auto", "cpu"), default="auto")
    export.add_argument("--height", type=int, default=0)
    export.add_argument("--device", type=int, default=0)
    export.add_argument("--jobs", type=int, default=1)
    export.add_argument("--preset", choices=("balanced", "fast", "max_speed"), default="balanced")
    args = parser.parse_args()
    emit, cancel = Emitter(sys.stdout), Event()
    try:
        if args.command == "gui":
            from .twitch_watch_gui import main as gui
            gui()
            return 0
        ffmpeg, ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
        if args.command == "diagnose":
            from importlib.metadata import PackageNotFoundError, version
            try:
                ytdlp = version("yt-dlp")
            except PackageNotFoundError:
                ytdlp = None
            emit("diagnosis", ffmpeg=ffmpeg, ffprobe=ffprobe, yt_dlp=ytdlp,
                 cuda=probe_cuda(ffmpeg, args.device) if ffmpeg else (False, "ffmpeg not found"))
            return 0
        if args.command == "record":
            from .worker import record as run
            return run(Settings.from_dict(json.loads(args.config.read_text(encoding="utf-8"))), cancel, emit)
        if not ffmpeg or not ffprobe:
            raise RuntimeError("ffmpeg and ffprobe are required")
        export_batch(args.files, ExportOptions(args.mode, args.height, args.device, args.jobs, args.preset),
                     ffmpeg, ffprobe, cancel, emit)
        return 0
    except KeyboardInterrupt:
        cancel.set()
        return 130
    except Exception as exc:
        emit("error", message=f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
