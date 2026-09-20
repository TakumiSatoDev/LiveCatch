"""Persistent YouTube/Twitch watch list and main-thread-owned recording scheduler.

Network checks run in bounded, killable worker processes, never in Tk callbacks.
A fresh Supervisor owns every probe/recording; old results cannot own a new job.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Thread
from time import monotonic
from typing import Callable
from urllib.parse import urlsplit
from uuid import uuid4

from .channels import broadcast_key, is_youtube, normalize_target, target_url, valid_broadcast_id
from .config import Settings
from .events import EventBuffer, redact
from .supervisor import Supervisor

WATCH_FILE = Path.home() / ".livecatch_twitch_watch.json"
MAX_CHANNELS = 50
MAX_PROBES = 2
PROBE_TIMEOUT = 45.0
MAX_ATTEMPTS = 3
MONITOR_PRESETS = {
    "manual": None,
    "balanced": (32, 3),
    "fast": (64, 4),
    "extreme": (96, 6),
    "max": (128, 8),
    "ultra": (192, 8),
    "experimental_256": (256, 8),
}
CATCHUP_MODES = ("from_start", "live_edge")
_RESERVED = {"videos", "directory", "downloads", "settings", "inventory", "subscriptions",
             "search", "login", "signup", "p", "jobs", "wallet", "friends", "turbo", "drops"}


def normalize_channel(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Twitch channel must be a name or channel URL")
    value = value.strip()
    if "://" in value or "/" in value or "." in value:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if (parsed.scheme not in ("http", "https")
                or parsed.hostname not in ("twitch.tv", "www.twitch.tv", "m.twitch.tv")
                or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
            raise ValueError("Use a Twitch channel URL, not a video/clip or another website")
        value = parsed.path.strip("/")
    else:
        value = value.removeprefix("@")
    value = value.lower()
    if not re.fullmatch(r"[a-z0-9_]{1,25}", value) or value in _RESERVED:
        raise ValueError("Use a Twitch channel name (letters, numbers, underscores), not a video URL")
    return value


def parse_channels(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(normalize_channel(x) for x in re.split(r"[\s,;、]+", text.strip()) if x))


@dataclass(frozen=True)
class WatchChannel:
    login: str
    enabled: bool = True


@dataclass(frozen=True)
class WatchConfig:
    channels: tuple[WatchChannel, ...] = ()
    interval: int = 60
    max_recordings: int = 3
    autostart: bool = False
    monitor_preset: str = "fast"
    catchup_mode: str = "from_start"
    schema_version: int = 1

    def validate(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported watch configuration version")
        if type(self.interval) is not int or not 30 <= self.interval <= 3600:
            raise ValueError("Watch interval must be 30..3600 seconds")
        if type(self.max_recordings) is not int or not 1 <= self.max_recordings <= 8:
            raise ValueError("Concurrent automatic recordings must be 1..8")
        if type(self.autostart) is not bool:
            raise ValueError("Invalid autostart flag")
        if self.monitor_preset not in MONITOR_PRESETS:
            raise ValueError("Invalid monitoring preset")
        if self.catchup_mode not in CATCHUP_MODES:
            raise ValueError("Invalid monitoring catch-up mode")
        if not isinstance(self.channels, tuple) or len(self.channels) > MAX_CHANNELS:
            raise ValueError(f"Register at most {MAX_CHANNELS} channels")
        seen = set()
        for channel in self.channels:
            if not isinstance(channel, WatchChannel) or type(channel.enabled) is not bool:
                raise ValueError("Invalid watch channel")
            if normalize_target(channel.login) != channel.login or channel.login in seen:
                raise ValueError("Channel names must be normalized and unique")
            seen.add(channel.login)

    @classmethod
    def from_dict(cls, data: dict) -> WatchConfig:
        if not isinstance(data, dict) or not isinstance(data.get("channels", []), list):
            raise ValueError("Invalid watch configuration")
        channels = []
        for item in data.get("channels", []):
            if not isinstance(item, dict):
                raise ValueError("Invalid watch channel")
            channels.append(WatchChannel(normalize_target(item.get("login")), item.get("enabled", True)))
        result = cls(tuple(channels), data.get("interval", 60), data.get("max_recordings", 3),
                     data.get("autostart", False), data.get("monitor_preset", "manual"),
                     data.get("catchup_mode", "from_start"), data.get("schema_version", 1))
        result.validate()
        return result


class WatchStore:
    def __init__(self, path: Path = WATCH_FILE):
        self.path = path

    def load(self) -> WatchConfig:
        return WatchConfig.from_dict(json.loads(self.path.read_text(encoding="utf-8"))) if self.path.exists() else WatchConfig()

    def save(self, config: WatchConfig) -> None:
        config.validate()
        old = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        if not isinstance(old, dict):
            raise ValueError("Back up the invalid watch file before replacing it")
        if self.path.exists():
            WatchConfig.from_dict(old)  # Do not silently destroy a corrupt/future-version file.
        old.update(asdict(config))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(old, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)


def apply_monitor_preset(base: Settings, preset: str) -> Settings:
    if preset not in MONITOR_PRESETS:
        raise ValueError("Invalid monitoring preset")
    values = MONITOR_PRESETS[preset]
    if values is None:
        return replace(base, gpu_export="off")
    fragments, prefetch = values
    return replace(base, concurrent_fragments=fragments, prefetch=prefetch, gpu_export="off")


def channel_settings(base: Settings, login: str, stream_id: str | None = None, *, catchup: bool = False) -> Settings:
    login = normalize_target(login)
    settings = replace(
        base, url=target_url(login), mode="reservation",
        live_from_start=bool(catchup), gpu_export="off",
    )
    if stream_id is not None:
        if not valid_broadcast_id(login, stream_id):
            raise ValueError("Missing/invalid broadcast ID")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        template = f"Twitch/{login}/{stream_id}_{stamp}_{uuid4().hex[:12]}/%(title)s.%(ext)s"
        if is_youtube(login):
            template = f"YouTube/%(channel_id)s/{stream_id}_{stamp}_{uuid4().hex[:12]}/%(title)s.%(ext)s"
            settings = replace(settings, url=f"https://www.youtube.com/watch?v={stream_id}")
        settings = replace(settings, output_template=template)
    return settings


def new_worker(kind: str, stream_id: str | None = None) -> Supervisor:
    worker = Supervisor()
    flags = {"probe": ["--twitch-probe"], "record": ["--twitch-watch-record", str(stream_id)],
             "youtube_probe": ["--youtube-probe"], "youtube_record": ["--youtube-watch-record", str(stream_id)]}
    if kind not in flags:
        raise ValueError("Unknown worker kind")
    worker.command += flags[kind]
    return worker


@dataclass
class Job:
    worker: Supervisor
    started: float
    generation: int
    terminal: dict | None = None
    result: dict | None = None
    error: str = ""
    discard: bool = False
    killer: Thread | None = None


@dataclass
class ChannelState:
    status: str = "idle"
    title: str = ""
    detail: str = ""
    checked: str = ""
    online: bool = False
    stream_id: str = ""
    suppressed_id: str = ""
    attempts: int = 0
    check_errors: int = 0
    next_check: float = 0.0
    observed: float = -1e20
    retry_at: float = 0.0
    probe: Job | None = None
    recording: Job | None = None
    last_elapsed: float = 0.0
    phase: str = ""
    progress: dict[str, dict] = field(default_factory=dict)


class WatchManager:
    """Call tick/configure/start/stop only on the owning GUI (or test) thread."""
    def __init__(self, factory: Callable = new_worker, clock: Callable = monotonic,
                 manual_channels: Callable = frozenset):
        self.factory, self.clock, self.manual_channels = factory, clock, manual_channels
        self.events = EventBuffer()
        self.config = WatchConfig()
        self.settings = Settings()
        self.states: dict[str, ChannelState] = {}
        self.running = False
        self.generation = 0
        self.finished_broadcasts = deque(maxlen=1000)

    @property
    def recording_channels(self) -> set[str]:
        return {name for name, state in self.states.items() if state.recording is not None}

    @property
    def has_children(self) -> bool:
        return any(s.probe is not None or s.recording is not None for s in self.states.values())

    def configure(self, config: WatchConfig, settings: Settings) -> None:
        config.validate()
        settings.validate(require_url=False)
        if self.running or any(s.probe is not None for s in self.states.values()):
            raise ValueError("Stop monitoring and let pending checks finish before editing")
        names = {c.login for c in config.channels}
        if self.recording_channels - names:
            raise ValueError("Stop a channel's recording before removing it")
        self.config, self.settings = config, settings
        self.states = {c.login: self.states.get(c.login, ChannelState()) for c in config.channels}

    def add_channels(self, channels: tuple[WatchChannel, ...], settings: Settings) -> None:
        """Add registrations while monitoring without disturbing existing jobs."""
        if not channels:
            return
        settings.validate(require_url=False)
        existing = {c.login for c in self.config.channels}
        additions = []
        for channel in channels:
            if not isinstance(channel, WatchChannel):
                raise ValueError("Invalid watch channel")
            if channel.login in existing:
                continue
            additions.append(channel)
            existing.add(channel.login)
        config = replace(self.config, channels=self.config.channels + tuple(additions))
        config.validate()
        self.config, self.settings = config, settings
        now = self.clock()
        for i, channel in enumerate(additions):
            state = ChannelState()
            state.next_check = now + i * 0.05
            state.status = "waiting" if channel.enabled else "disabled"
            self.states[channel.login] = state
            self._log(channel.login, "Registration added; checking the current broadcast immediately")
        # If monitoring is paused, additions are simply persisted for the next start.

    def elapsed_seconds(self, login: str) -> float:
        state = self.states[login]
        if state.recording is not None:
            return max(0.0, self.clock() - state.recording.started)
        return max(0.0, state.last_elapsed)

    def start(self) -> None:
        if self.running:
            return
        if not any(c.enabled for c in self.config.channels):
            raise ValueError("Register and enable at least one YouTube/Twitch channel")
        if any(s.probe is not None for s in self.states.values()):
            raise ValueError("Previous checks are still shutting down")
        self.running = True
        self.generation += 1
        for i, c in enumerate(self.config.channels):
            s = self.states[c.login]
            s.next_check = self.clock() + i * 0.25
            s.online = False  # A restart requires a fresh observation, not a cached live flag.
            if s.recording is None:
                s.status = "waiting" if c.enabled else "disabled"

    def _log(self, login: str, message: str) -> None:
        self.events.put({"event": "watch_log", "message": f"[Watch:{login}] {redact(message)[:1800]}"})

    def _kill(self, login: str, job: Job) -> None:
        if job.killer is not None and job.killer.is_alive():
            return
        def kill():
            try:
                job.worker.force_stop()
            except (OSError, RuntimeError) as exc:
                self._log(login, f"Unable to stop worker: {exc}")
        job.killer = Thread(target=kill, daemon=True, name="lc-watch-stop")
        job.killer.start()

    def stop(self) -> None:
        """Pause monitoring. Existing recordings/mux/exports continue unchanged."""
        self.running = False
        self.generation += 1
        for login, s in self.states.items():
            if s.probe is not None:
                s.probe.discard = True
                self._kill(login, s.probe)
            if s.recording is None:
                s.status = "paused"

    def stop_recording(self, login: str, *, force: bool = False) -> None:
        s = self.states[login]
        s.suppressed_id = s.stream_id
        if s.stream_id:
            self.finished_broadcasts.append(broadcast_key(login, s.stream_id))
        s.status = "stopping" if s.recording else "skipped"
        if s.recording:
            if force:
                self._kill(login, s.recording)
            else:
                s.recording.worker.stop()

    def retry_channel(self, login: str) -> None:
        s = self.states[login]
        if s.recording is not None:
            raise ValueError("Stop the current recording before retrying")
        key = broadcast_key(login, s.stream_id)
        self.finished_broadcasts = deque((x for x in self.finished_broadcasts if x != key), maxlen=1000)
        s.suppressed_id, s.attempts, s.retry_at = "", 0, 0.0
        s.online, s.next_check, s.status = False, self.clock(), "waiting"

    def shutdown(self, *, force: bool = False) -> None:
        self.stop()
        for login in self.recording_channels:
            self.stop_recording(login, force=force)

    @staticmethod
    def _finished(job: Job) -> bool:
        # force_stop may hold Supervisor.lock while taskkill exits on Windows.
        if job.killer is not None and job.killer.is_alive():
            return False
        return job.terminal is not None and not job.worker.active

    def _check_failed(self, s: ChannelState, now: float, message: str) -> None:
        s.online = False  # Error is unknown, not evidence of an offline/online transition.
        s.check_errors += 1
        s.status, s.detail = "check_error", redact(message)[:400]
        s.next_check = now + min(self.config.interval * 2 ** min(s.check_errors - 1, 5), 3600)

    def _drain_probe(self, login: str, s: ChannelState, now: float) -> None:
        job = s.probe
        if job is None:
            return
        for event in job.worker.events.drain():
            kind = event.get("event")
            if kind == "probe_result":
                job.result = event
            elif kind == "done":
                job.terminal = event
            elif kind == "error":
                job.error = str(event.get("message", "Probe failed"))
        if not self._finished(job):
            if now - job.started >= PROBE_TIMEOUT and not job.discard:
                job.discard = True
                self._check_failed(s, now, "Live check timed out")
                self._kill(login, job)
            return
        s.probe = None
        if job.discard or job.generation != self.generation or not self.running:
            return
        s.checked = datetime.now().strftime("%H:%M:%S")
        if job.terminal.get("status") != "completed" or not isinstance(job.result, dict):
            self._check_failed(s, now, job.error or "Live check failed (not treated as offline)")
            return
        result = job.result
        if result.get("status") in ("offline", "upcoming"):
            s.online = False
            s.status, s.detail = result["status"], ""
        elif result.get("status") == "live" and valid_broadcast_id(login, result.get("stream_id", "")):
            stream_id = str(result["stream_id"])
            if stream_id != s.stream_id:
                s.attempts, s.retry_at = 0, 0.0
            s.stream_id, s.online, s.observed = stream_id, True, now
            s.title, s.detail = str(result.get("title", ""))[:300], ""
            s.status = "live"
        else:
            self._check_failed(s, now, "Invalid live probe result")
            return
        s.check_errors = 0
        s.next_check = now + self.config.interval

    def _drain_recording(self, login: str, s: ChannelState, now: float) -> None:
        job = s.recording
        if job is None:
            return
        for event in job.worker.events.drain():
            kind = event.get("event")
            if kind == "done":
                job.terminal = event
            elif kind == "phase" and s.status != "stopping":
                phase = str(event.get("name") or "")
                s.phase = phase
                s.status = {
                    "starting": "record_starting",
                    "extracting": "record_extracting",
                    "downloading": "recording",
                    "postprocessing": "postprocessing",
                    "exporting": "postprocessing",
                }.get(phase, "recording")
            elif kind in ("progress", "fragment"):
                stream = str(event.get("stream") or "media")
                progress = s.progress.setdefault(stream, {})
                if kind == "progress":
                    for key in ("percent", "downloaded_bytes", "total_bytes", "speed",
                                "fragment_index", "fragment_count"):
                        value = event.get(key)
                        if value is not None:
                            progress[key] = value
                else:
                    current = event.get("current")
                    total = event.get("total")
                    if isinstance(current, int):
                        progress["fragment_index"] = current
                    if isinstance(total, int) and total > 0:
                        progress["fragment_count"] = total
                        if isinstance(current, int):
                            progress["percent"] = min(100.0, max(0.0, current * 100 / total))
                    size = event.get("bytes")
                    if isinstance(size, int) and size >= 0:
                        progress["committed_bytes"] = int(progress.get("committed_bytes", 0)) + size
            elif kind in ("error", "warning", "output", "exported"):
                message = str(event.get("message") or event.get("path", kind))
                s.detail = redact(message)[:400]
                self._log(login, message)
        if not self._finished(job):
            return
        s.recording = None
        s.last_elapsed = max(0.0, now - job.started)
        result = job.terminal.get("status", "failed")
        if result in ("completed", "cancelled", "forced") or s.suppressed_id == s.stream_id:
            s.suppressed_id = s.stream_id
            self.finished_broadcasts.append(broadcast_key(login, s.stream_id))
            s.status = "completed" if result == "completed" else "skipped"
        else:
            s.retry_at = now + max(self.config.interval, min(60 * 2 ** max(0, s.attempts - 1), 900))
            s.status = "retry_limit" if s.attempts >= MAX_ATTEMPTS else "retry_wait"
        s.online = False
        s.next_check = now + self.config.interval
        self._log(login, f"Recording {result} after {s.last_elapsed:.1f}s; waiting for the next eligible broadcast/check")

    def tick(self) -> None:
        now = self.clock()
        for login, s in self.states.items():
            self._drain_probe(login, s, now)
            self._drain_recording(login, s, now)
        if not self.running:
            return
        manual = set(self.manual_channels())
        for c in self.config.channels:
            s = self.states[c.login]
            if not c.enabled or s.recording is not None:
                continue
            if s.online:
                key = broadcast_key(c.login, s.stream_id)
                if s.stream_id == s.suppressed_id or key in self.finished_broadcasts:
                    s.status = "skipped"
                elif s.attempts >= MAX_ATTEMPTS:
                    s.status = "retry_limit"
                elif c.login in manual or key in manual:
                    s.status = "manual"
                elif any(broadcast_key(name, other.stream_id) == key
                         for name, other in self.states.items() if other.recording is not None):
                    s.status = "duplicate"
                elif now < s.retry_at:
                    s.status = "retry_wait"
                elif len(self.recording_channels) >= self.config.max_recordings:
                    s.status = "queued"
                elif now - s.observed <= self.config.interval:
                    try:
                        s.attempts += 1
                        worker = self.factory("youtube_record" if is_youtube(c.login) else "record", s.stream_id)
                        worker.start(channel_settings(
                            self.settings, c.login, s.stream_id,
                            catchup=self.config.catchup_mode == "from_start",
                        ))
                        s.recording = Job(worker, now, self.generation)
                        s.phase = "starting"
                        s.progress.clear()
                        s.last_elapsed = 0.0
                        s.status = "record_starting"
                        self._log(c.login, f"Started broadcast {s.stream_id} (attempt {s.attempts}/{MAX_ATTEMPTS})")
                    except Exception as exc:
                        s.retry_at = now + max(60, self.config.interval)
                        s.status, s.detail = "retry_wait", redact(str(exc))[:400]
                        self._log(c.login, f"Recording could not start: {exc}")
        # Oldest-due-first avoids starving later channels when checks are slow.
        eligible = [c for c in self.config.channels if c.enabled]
        eligible.sort(key=lambda c: self.states[c.login].next_check)
        for c in eligible:
            s = self.states[c.login]
            if s.recording is not None or s.probe is not None or now < s.next_check:
                continue
            if sum(x.probe is not None for x in self.states.values()) >= MAX_PROBES:
                break
            try:
                worker = self.factory("youtube_probe" if is_youtube(c.login) else "probe", None)
                worker.start(channel_settings(self.settings, c.login))
                s.probe = Job(worker, now, self.generation)
                if not s.online:
                    s.status = "checking"
            except Exception as exc:
                self._check_failed(s, now, str(exc))
                self._log(c.login, f"Check could not start: {exc}")
