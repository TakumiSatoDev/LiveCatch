"""Optional GPU sidecar exports. Originals are never re-encoded or replaced.

CUDA handles frame scaling; NVDEC/NVENC are dedicated video engines, not CUDA
kernels. Network downloads and stream-copy muxing deliberately do not use a GPU.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import CancelledError
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Event, Thread
from typing import Callable

from .parallel import OrderedPrefetch


def hidden_process() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def probe_cuda(ffmpeg: str, device: int = 0) -> tuple[bool, str]:
    """Perform a real encode + CUDA-scale smoke test, not just `-encoders`."""
    cmd = [ffmpeg, "-hide_banner", "-v", "error", "-init_hw_device", f"cuda=lc:{device}",
           "-filter_hw_device", "lc", "-f", "lavfi", "-i", "color=s=128x128:r=1:d=1",
           "-vf", "format=nv12,hwupload,scale_cuda=64:64:format=yuv420p", "-frames:v", "1",
           "-c:v", "h264_nvenc", "-gpu", str(device), "-f", "null", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=15, **hidden_process())
        return result.returncode == 0, (result.stderr.strip()[-4000:] or "CUDA/NVENC probe succeeded")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def probe_media(ffprobe: str, source: Path) -> dict:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        check=True, **hidden_process())
    info = json.loads(result.stdout)
    videos = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not videos:
        raise ValueError("No video stream found")
    if videos[0].get("color_transfer") in ("smpte2084", "arib-std-b67"):
        raise ValueError("HDR export needs explicit tone mapping; the original is preserved")
    return info


@dataclass(frozen=True)
class ExportOptions:
    mode: str = "auto"
    height: int = 0
    device: int = 0
    jobs: int = 1
    preset: str = "balanced"

    def validate(self):
        if self.mode not in ("auto", "cuda", "cpu"):
            raise ValueError("Export mode must be auto, cuda or cpu")
        if self.height not in (0, 360, 480, 720, 1080, 1440, 2160):
            raise ValueError("Unsupported export height")
        if not 0 <= self.device <= 31 or not 1 <= self.jobs <= 8:
            raise ValueError("Invalid GPU device / job count")
        if self.preset not in ("balanced", "fast", "max_speed"):
            raise ValueError("Invalid GPU speed preset")


def export_command(ffmpeg: str, source: Path, target: Path, options: ExportOptions,
                   backend: str) -> list[str]:
    options.validate()
    if backend not in ("cuda", "cpu"):
        raise ValueError("A resolved backend is required")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]  # Only our private temp file.
    if backend == "cuda":
        cmd += ["-hwaccel", "cuda", "-hwaccel_device", str(options.device),
                "-hwaccel_output_format", "cuda"]
    cmd += ["-i", str(source), "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0",
            "-map_chapters", "0"]
    height = str(options.height) if options.height else "ih"
    if backend == "cuda":
        nvenc_preset = {"balanced": "p4", "fast": "p2", "max_speed": "p1"}[options.preset]
        cmd += ["-vf", f"scale_cuda=w=-2:h={height}:format=yuv420p", "-c:v", "h264_nvenc",
                "-gpu", str(options.device), "-preset", nvenc_preset,
                "-rc", "vbr", "-cq", "23", "-b:v", "0"]
    else:
        cmd += ["-vf", f"scale=w=-2:h={height},format=yuv420p", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "23", "-threads", "2"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target)]
    return cmd


def run_media_process(cmd: list[str], cancel: Event, emit: Callable) -> None:
    if cancel.is_set():
        raise CancelledError()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", **hidden_process())
    tail = deque(maxlen=40)

    def read():
        if proc.stdout is not None:
            for line in proc.stdout:
                tail.append(line[:2000])
            proc.stdout.close()

    reader = Thread(target=read, daemon=True)
    reader.start()
    try:
        while proc.poll() is None:
            if cancel.wait(0.1):
                try:
                    proc.stdin.write("q\n")
                    proc.stdin.flush()
                    proc.wait(timeout=5)
                except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                raise CancelledError()
        reader.join(timeout=1)
        if proc.returncode:
            raise RuntimeError(f"ffmpeg exited {proc.returncode}: {''.join(tail)[-5000:]}")
    finally:
        if proc.stdin:
            proc.stdin.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        reader.join(timeout=1)


def export_one(source: Path, target: Path, options: ExportOptions, backend: str,
               ffmpeg: str, ffprobe: str, cancel: Event, emit: Callable) -> Path:
    source, target = source.resolve(), target.resolve()
    if source == target or target.exists():
        raise FileExistsError("Exports may not overwrite any existing file")
    if not source.is_file():
        raise FileNotFoundError(source)
    probe_media(ffprobe, source)
    if cancel.is_set():
        raise CancelledError()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".livecatch-", suffix=".part.mp4", dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        try:
            emit("export", path=str(source), backend=backend)
            run_media_process(export_command(ffmpeg, source, temporary, options, backend), cancel, emit)
        except RuntimeError:
            if options.mode != "auto" or backend != "cuda" or cancel.is_set():
                raise
            emit("warning", message="GPU export failed for this file; retrying on CPU. Original preserved.")
            run_media_process(export_command(ffmpeg, source, temporary, options, "cpu"), cancel, emit)
        if cancel.is_set():
            raise CancelledError()
        probe_media(ffprobe, temporary)
        if temporary.stat().st_size == 0:
            raise RuntimeError("ffmpeg produced an empty file")
        # Same-directory hard-link publication is atomic AND refuses clobbering.
        # Filesystems without hard-link support fail safely rather than overwriting.
        os.link(temporary, target)
        emit("exported", path=str(target))
        return target
    finally:
        temporary.unlink(missing_ok=True)


def export_batch(sources: list[Path], options: ExportOptions, ffmpeg: str, ffprobe: str,
                 cancel: Event, emit: Callable) -> list[Path]:
    options.validate()
    backend = options.mode
    if backend != "cpu":
        supported, detail = probe_cuda(ffmpeg, options.device)
        if not supported and backend == "cuda":
            raise RuntimeError("CUDA/NVENC unavailable: " + detail)
        backend = "cuda" if supported else "cpu"
        if not supported:
            emit("warning", message="CUDA unavailable; using CPU: " + detail)
    sources = list(dict.fromkeys(p.resolve() for p in sources))

    total = len(sources)

    def run(item):
        index, source = item
        target = source.with_name(source.stem + ".export.mp4")
        emit("export_batch", current=index, total=total, path=str(source), backend=backend)
        return export_one(source, target, options, backend, ffmpeg, ffprobe, cancel, emit)

    with OrderedPrefetch(options.jobs, options.jobs, cancel) as pool:
        return list(pool.map(run, enumerate(sources, start=1)))
