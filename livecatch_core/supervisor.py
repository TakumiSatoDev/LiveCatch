"""Subprocess lifecycle isolated from Tk. No delayed timer can kill a new job."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Lock, Thread

from .config import Settings
from .events import EventBuffer, redact
from .tools import app_dir


class Supervisor:
    def __init__(self, command: list[str] | None = None):
        self.events = EventBuffer()
        self.lock = Lock()
        self.proc = None
        self._active = False
        self._forced = False
        if command is not None:
            self.command = command
        elif getattr(sys, "frozen", False):
            self.command = [str(app_dir() / "LiveCatchWorker.exe")]
        else:
            self.command = [sys.executable, "-u", str(app_dir() / "livecatch_worker.py")]

    @property
    def active(self) -> bool:
        with self.lock:
            return self._active

    def start(self, settings: Settings) -> None:
        settings.validate()
        with self.lock:
            if self._active:
                raise RuntimeError("A recording is already active")
            self._active = True
            self._forced = False
        try:
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                    errors="replace", bufsize=1, env=env, **flags)
            with self.lock:
                self.proc = proc
            proc.stdin.write(json.dumps(settings.to_dict(), ensure_ascii=False) + "\n")
            proc.stdin.flush()
            Thread(target=self._read, args=(proc,), daemon=True, name="lc-events").start()
        except BaseException:
            with self.lock:
                self._active = False
                proc = self.proc
                self.proc = None
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()
            raise

    def _read(self, proc) -> None:
        terminal = None
        try:
            for line in proc.stdout:
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict) or not isinstance(event.get("event"), str):
                        raise ValueError("Not an event")
                except ValueError:
                    event = {"event": "log", "message": redact(line.rstrip())[:8000]}
                if event["event"] == "done":
                    terminal = event
                else:
                    self.events.put(event)
            code = proc.wait()
            if terminal is None:
                terminal = {"event": "done", "status": "failed", "code": code}
            if code and terminal.get("status") == "completed":
                terminal = {"event": "done", "status": "failed", "code": code}
        finally:
            proc.stdout.close()
            if proc.stdin:
                proc.stdin.close()
            with self.lock:
                if self.proc is proc:
                    if self._forced:
                        terminal = {"event": "done", "status": "forced", "code": proc.returncode}
                    self.proc = None
                    self._active = False
            self.events.put(terminal or {"event": "done", "status": "failed", "code": -1})

    def stop(self) -> None:
        with self.lock:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                return
            try:
                proc.stdin.write('{"command":"stop"}\n')
                proc.stdin.flush()
            except (OSError, ValueError):
                pass

    def force_stop(self) -> None:
        """Explicit, destructive cancellation of THIS child and its descendants."""
        with self.lock:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                return
            self._forced = True
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
                               check=True)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
