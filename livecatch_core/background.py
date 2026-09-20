"""Opt-in Windows tray/startup and a process-held lock shared by GUI/headless.

No GUI imports here. Tray callbacks only enqueue commands; Tk owns all UI calls.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
import tempfile
from threading import Event, Thread
from typing import Callable

BACKGROUND_FILE = Path.home() / ".livecatch_background.json"
INSTANCE_FILE = Path.home() / ".livecatch.instance.lock"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "LiveCatchAutoRecord"


@dataclass(frozen=True)
class BackgroundConfig:
    close_to_tray: bool = False
    start_hidden: bool = False

    def validate(self):
        if type(self.close_to_tray) is not bool or type(self.start_hidden) is not bool:
            raise ValueError("Background settings must be booleans")


class BackgroundStore:
    def __init__(self, path: Path = BACKGROUND_FILE):
        self.path = path

    def load(self) -> BackgroundConfig:
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError("Invalid background settings")
        value = BackgroundConfig(data.get("close_to_tray", False), data.get("start_hidden", False))
        value.validate()
        return value

    def save(self, value: BackgroundConfig):
        value.validate()
        self.load()  # Do not silently overwrite a corrupt file.
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        data.update(asdict(value))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(data, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(filename, self.path)
        finally:
            Path(filename).unlink(missing_ok=True)


class InstanceLease:
    """Advisory OS lock, automatically released after a crash; never unlink it."""
    def __init__(self, path: Path = INSTANCE_FILE):
        self.path = path
        self.file = None

    def acquire(self):
        if self.file is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self.path.stat().st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("LiveCatch is already running. Open its tray icon; do not start a second recorder.") from exc
        self.file = handle
        return self

    def release(self):
        handle, self.file = self.file, None
        if handle is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


def startup_command(executable=None, script=None, frozen=None) -> str:
    executable = Path(executable or sys.executable).resolve()
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        args = [str(executable), "--background"]
    else:
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.is_file():
            executable = pythonw
        script = Path(script or Path(__file__).resolve().parent.parent / "livecatch.py").resolve()
        if not executable.is_file() or not script.is_file():
            raise ValueError("The Python interpreter or LiveCatch entry point is missing")
        args = [str(executable), str(script), "--background"]
    command = subprocess.list2cmdline(args)
    if len(command) > 260:
        raise ValueError("Startup command exceeds Windows Run's 260-character limit; use a shorter install path")
    return command


class WindowsStartup:
    """Only the current user's single LiveCatch value is read/changed."""
    def __init__(self, registry=None, platform=None):
        self.platform = sys.platform if platform is None else platform
        self.registry = registry

    def _registry(self):
        if self.platform != "win32":
            raise RuntimeError("Login startup is supported on Windows only")
        if self.registry is None:
            import winreg
            self.registry = winreg
        return self.registry

    def enabled(self) -> bool:
        reg = self._registry()
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
                value, _ = reg.QueryValueEx(key, RUN_NAME)
                return bool(value)
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled: bool, command: str | None = None):
        if type(enabled) is not bool:
            raise ValueError("Invalid startup flag")
        reg = self._registry()
        if enabled:
            command = command or startup_command()
            if len(command) > 260 or "\0" in command:
                raise ValueError("Invalid startup command")
            with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                reg.SetValueEx(key, RUN_NAME, 0, reg.REG_SZ, command)
        else:
            try:
                with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                    reg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


def windows_icon(commands: Queue, language: str):
    if sys.platform != "win32":
        raise RuntimeError("Tray mode currently supports Windows. Use the headless watch command on other systems.")
    # Lazy imports allow normal recording/headless use without a desktop backend.
    import pystray
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((3, 3, 61, 61), radius=13, fill=(25, 105, 175, 255))
    draw.ellipse((19, 19, 45, 45), fill=(255, 255, 255, 255))
    titles = (("表示", "Open", "open"), ("監視開始", "Start monitoring", "start"),
              ("監視だけ停止", "Pause monitoring", "pause"),
              ("全自動録画を停止", "Stop automatic recordings", "stop"),
              ("終了", "Exit", "exit"))
    def action(command):
        def send(_icon, _item):
            commands.put((command, ""))
        return send
    menu = pystray.Menu(*(pystray.MenuItem(en if language == "en" else ja, action(command), default=i == 0)
                          for i, (ja, en, command) in enumerate(titles)))
    return pystray.Icon("LiveCatch", image, "LiveCatch", menu)


class TrayController:
    def __init__(self, factory: Callable = windows_icon):
        self.factory = factory
        self.commands = Queue(maxsize=0)
        self.ready = Event()
        self.closed = Event()
        self.thread = None
        self.icon = None

    def start(self, language="ja"):
        if self.closed.is_set():
            raise RuntimeError("Tray has been stopped")
        if self.thread is not None and self.thread.is_alive():
            return
        self.ready.clear()
        failed = Event()
        def report_failure(message):
            self.ready.clear()
            if not failed.is_set() and not self.closed.is_set():
                failed.set()
                self.commands.put(("failed", message))
        def run():
            try:
                self.icon = self.factory(self.commands, language)
                def setup(icon):
                    # pystray invokes setup on another thread: catch failures HERE.
                    try:
                        if self.closed.is_set():
                            icon.stop()
                            return
                        icon.visible = True
                        self.ready.set()
                        self.commands.put(("ready", ""))
                    except Exception as exc:
                        report_failure(f"{type(exc).__name__}: {exc}")
                        icon.stop()
                self.icon.run(setup=setup)
                report_failure("Tray event loop stopped")
            except Exception as exc:
                report_failure(f"{type(exc).__name__}: {exc}")
            finally:
                self.ready.clear()
        self.thread = Thread(target=run, daemon=True, name="lc-tray")
        self.thread.start()

    def stop(self):
        self.closed.set()
        self.ready.clear()
        if self.icon is not None:
            self.icon.stop()
