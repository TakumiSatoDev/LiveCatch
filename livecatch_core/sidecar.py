"""Recording sidecar: live chat, stream statistics and subtitles.

The recording worker patches yt-dlp's ``FragmentFD`` in-process, and YouTube's
live chat downloader is itself a ``FragmentFD``. Chat therefore runs in this
separate, unpatched process. A sidecar failure never fails the recording.

Protocol: the first stdin line is a JSON job, later lines are commands
(``{"command": "stop"}``); EOF also means stop. Stdout is JSON-lines events.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from threading import Event, Lock, Thread
from typing import Callable

from .events import Emitter, redact
from .tools import find_tool


@dataclass(frozen=True)
class Job:
    service: str                 # youtube | twitch
    url: str                     # canonical single-video URL
    base: str                    # output path without extension
    live: bool
    chat: bool = True
    stats: bool = False
    subtitles: bool = False
    stats_interval: int = 60
    twitch_login: str = ""
    cookies_browser: str = ""

    def validate(self) -> None:
        if self.service not in ("youtube", "twitch"):
            raise ValueError("Invalid sidecar service")
        if not self.base or not Path(self.base).is_absolute():
            raise ValueError("Sidecar base path must be absolute")
        if not 15 <= self.stats_interval <= 3600:
            raise ValueError("Invalid stats interval")

    @classmethod
    def from_dict(cls, data: dict) -> Job:
        if not isinstance(data, dict):
            raise ValueError("Sidecar job must be an object")
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        job = cls(**known)
        job.validate()
        return job


class QuietLogger:
    def __init__(self, log: Callable[[str], None] | None = None):
        self.log = log

    def debug(self, *_): pass
    def info(self, *_): pass

    def warning(self, message):
        if self.log and "Live chat download runs until" not in str(message):
            self.log(str(message))

    def error(self, message):
        if self.log:
            self.log(str(message))


def _base_options(job: Job, log) -> dict:
    options = {
        "quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True,
        "noplaylist": True, "live_from_start": False, "socket_timeout": 15,
        "retries": 3, "fragment_retries": 10, "extractor_retries": 2,
        "logger": QuietLogger(log), "ignore_no_formats_error": True,
        "windowsfilenames": os.name == "nt", "nopart": True, "cachedir": False,
    }
    if job.cookies_browser:
        options["cookiesfrombrowser"] = (job.cookies_browser,)
    deno = find_tool("deno")
    if deno:
        options["js_runtimes"] = {"deno": {"path": deno}}
    return options


def _outtmpl(base: str) -> str:
    return base.replace("%", "%%") + ".%(ext)s"


def chat_target(base: str) -> tuple[Path, str]:
    """Return (final chat file, base to download into).

    A retry of the same broadcast must not overwrite chat already captured, so
    later attempts download into ``base.N`` and are appended on finalize.
    """
    final = Path(base + ".live_chat.json")
    if not final.exists() or final.stat().st_size == 0:
        return final, base
    n = 1
    while Path(f"{base}.{n}.live_chat.json").exists():
        n += 1
    return final, f"{base}.{n}"


def append_and_remove(source: Path, target: Path) -> None:
    if source == target or not source.exists():
        return
    with target.open("ab") as out, source.open("rb") as src:
        data = src.read()
        if data and not data.endswith(b"\n"):
            data = data[:data.rfind(b"\n") + 1]  # drop a torn final line
        out.write(data)
    source.unlink()


def run_youtube_chat(job: Job, stop: Event, log, ydl_factory=None) -> None:
    import yt_dlp

    final, download_base = chat_target(job.base)

    def hook(_progress):
        if stop.is_set():
            raise KeyboardInterrupt()

    options = _base_options(job, log)
    options.update({"writesubtitles": True, "subtitleslangs": ["live_chat"],
                    "outtmpl": _outtmpl(download_base), "progress_hooks": [hook]})
    factory = ydl_factory or yt_dlp.YoutubeDL
    try:
        with factory(options) as ydl:
            ydl.download([job.url])
    except KeyboardInterrupt:
        pass
    finally:
        append_and_remove(Path(download_base + ".live_chat.json"), final)
    if final.exists():
        log(f"Chat saved: {final.name}")


def run_subtitles(job: Job, stop: Event, log, ydl_factory=None) -> None:
    import yt_dlp

    if stop.is_set():
        return
    options = _base_options(job, log)
    options.update({"writesubtitles": True, "subtitleslangs": ["all", "-live_chat"],
                    "outtmpl": _outtmpl(job.base), "ignoreerrors": True, "overwrites": False})
    with (ydl_factory or yt_dlp.YoutubeDL)(options) as ydl:
        ydl.download([job.url])


def run_twitch_chat(job: Job, stop: Event, log, connect=None) -> None:
    from .twitch_chat import TwitchChatRecorder, default_connect

    path = Path(job.base + ".chat.jsonl")
    recorder = TwitchChatRecorder(job.twitch_login, path, stop,
                                  connect=connect or default_connect, log=log)
    count = recorder.run()
    log(f"Twitch chat saved: {path.name} (+{count})")


def viewers_of(info: dict) -> int | None:
    for key in ("concurrent_view_count", "view_count"):
        value = info.get(key)
        if type(value) is int and value >= 0:
            return value
    return None


def probe_stats(job: Job, log) -> dict:
    import yt_dlp

    options = _base_options(job, None)
    options.update({"retries": 0, "extractor_retries": 0, "socket_timeout": 10})
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(job.url, download=False, process=False)
    if not isinstance(info, dict):
        return {}
    return {"viewers": viewers_of(info), "title": str(info.get("title") or "")[:300],
            "live": info.get("is_live") is True}


def run_stats(job: Job, stop: Event, log, probe=probe_stats, clock=time.time, wait=None) -> None:
    wait = wait or stop.wait
    path = Path(job.base + ".stats.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    misses = 0
    with path.open("a", encoding="utf-8", newline="\n") as out:
        while not stop.is_set():
            started = clock()
            try:
                sample = probe(job, log)
            except Exception as exc:  # network/extractor hiccups are expected
                sample = {}
                log(f"Stats sample failed: {type(exc).__name__}")
            if sample:
                record = {"t": int(started * 1000), **{k: v for k, v in sample.items() if v is not None}}
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
            misses = 0 if sample.get("live") else misses + 1
            if misses >= 3:
                log("Stats: broadcast no longer live; sampling stopped")
                return
            wait(max(1.0, job.stats_interval - (clock() - started)))


def tasks_for(job: Job) -> list[tuple[str, Callable]]:
    tasks: list[tuple[str, Callable]] = []
    if job.service == "youtube":
        if job.subtitles and not job.live:
            tasks.append(("subtitles", run_subtitles))
        if job.chat:
            tasks.append(("chat", run_youtube_chat))
    elif job.chat and job.live and job.twitch_login:
        tasks.append(("chat", run_twitch_chat))
    if job.stats and job.live:
        tasks.append(("stats", run_stats))
    return tasks


def run(job: Job, stop: Event, emit, *, tasks=None, grace: float = 20.0) -> int:
    """Run all tasks until they finish naturally or ``stop`` is set."""
    def log(message: str) -> None:
        emit("log", message=f"[sidecar] {redact(message)[:500]}")

    threads = []
    for name, func in (tasks if tasks is not None else tasks_for(job)):
        def body(name=name, func=func):
            try:
                func(job, stop, log)
            except Exception as exc:
                log(f"{name} failed: {type(exc).__name__}: {exc}")
        thread = Thread(target=body, daemon=True, name=f"lc-sidecar-{name}")
        thread.start()
        threads.append(thread)
    while any(t.is_alive() for t in threads) and not stop.wait(0.5):
        pass
    deadline = time.monotonic() + grace
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return 0


def main() -> int:
    emit = Emitter(sys.stdout)
    stop = Event()
    try:
        job = Job.from_dict(json.loads(sys.stdin.readline()))

        def control():
            for line in sys.stdin:
                try:
                    if json.loads(line).get("command") == "stop":
                        break
                except (ValueError, AttributeError):
                    continue
            stop.set()

        Thread(target=control, daemon=True, name="lc-sidecar-control").start()
        code = run(job, stop, emit)
    except Exception as exc:
        emit("log", message=f"[sidecar] {type(exc).__name__}: {exc}")
        code = 1
    emit("done", status="completed" if code == 0 else "failed", code=code)
    return code


# --- Worker side ----------------------------------------------------------

def sidecar_command() -> tuple[list[str], dict]:
    env = os.environ.copy()
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
    if getattr(sys, "frozen", False):
        return [sys.executable, "--sidecar"], env
    root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return [sys.executable, "-u", "-m", "livecatch_core.sidecar"], env


class SidecarProcess:
    """Owns one sidecar child for the duration of a recording."""

    def __init__(self, job: Job, emit, *, command: list[str] | None = None, env: dict | None = None):
        job.validate()
        self.job = job
        self.emit = emit
        self.lock = Lock()
        default_command, default_env = sidecar_command()
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        self.proc = subprocess.Popen(
            command or default_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            bufsize=1, env=env or default_env, **flags)
        self.proc.stdin.write(json.dumps(job.__dict__, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        self.reader = Thread(target=self._read, daemon=True, name="lc-sidecar-events")
        self.reader.start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                event = {"event": "log", "message": line.rstrip()}
            if isinstance(event, dict) and event.get("event") == "log" and event.get("message"):
                self.emit("log", message=str(event["message"])[:1000])

    def stop(self) -> None:
        with self.lock:
            if self.proc.poll() is None and self.proc.stdin and not self.proc.stdin.closed:
                try:
                    self.proc.stdin.write(json.dumps({"command": "stop"}) + "\n")
                    self.proc.stdin.flush()
                    self.proc.stdin.close()
                except OSError:
                    pass

    def wait(self, timeout: float | None = None, cancel: Event | None = None) -> bool:
        """Wait for exit. ``cancel`` turns a natural wait into a stop."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.proc.poll() is None:
            if cancel is not None and cancel.is_set():
                self.stop()
                cancel = None
                deadline = time.monotonic() + 30
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.2)
        self.reader.join(2)
        return True

    def close(self, timeout: float = 30.0) -> None:
        self.stop()
        if not self.wait(timeout):
            self.proc.kill()
            self.proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
