"""Small helper that replaces the installed files after LiveCatch exits."""
from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


REPLACE_FILES = {"LiveCatch.exe", "LiveCatchWorker.exe", "LiveCatchUpdater.exe"}
INSTALL_RETRY_SECONDS = 90.0


def _wait_for_parent(pid: int) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            kernel32.WaitForSingleObject(handle, 30_000)
            kernel32.CloseHandle(handle)
        return
    for _ in range(300):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)


def _payload_files(payload: Path) -> list[tuple[Path, Path]]:
    files = []
    for source in payload.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(payload)
        if relative.name in REPLACE_FILES or relative.parts[:1] == ("tools",):
            files.append((source, relative))
    required = {"LiveCatch.exe", "LiveCatchWorker.exe"}
    if not required.issubset({relative.name for _, relative in files}):
        raise RuntimeError("Update payload is missing the LiveCatch executables")
    return files


def _install(payload: Path, target: Path) -> None:
    files = _payload_files(payload)
    backup = Path(tempfile.mkdtemp(prefix="livecatch-backup-", dir=str(target.parent)))
    try:
        for source, relative in files:
            destination = target / relative
            if destination.is_file():
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, saved)
        for source, relative in files:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.livecatch-new")
            temporary.unlink(missing_ok=True)
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
    except Exception:
        for _, relative in files:
            saved = backup / relative
            destination = target / relative
            if saved.is_file():
                try:
                    os.replace(saved, destination)
                except OSError:
                    shutil.copy2(saved, destination)
        raise
    finally:
        for _, relative in files:
            temporary = target / relative
            temporary = temporary.with_name(f".{temporary.name}.livecatch-new")
            temporary.unlink(missing_ok=True)
        shutil.rmtree(backup, ignore_errors=True)


def _install_with_retry(payload: Path, target: Path, *, timeout: float = INSTALL_RETRY_SECONDS) -> None:
    """Install after Windows releases image/file handles left by the old app.

    The GUI waits for the application process, but Windows can keep an image
    section or a worker/tool handle locked briefly after process termination.
    Retry only sharing/access errors; missing files and malformed payloads must
    fail immediately instead of being hidden by a retry loop.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    delay = 0.25
    while True:
        try:
            _install(payload, target)
            return
        except PermissionError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32):
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 1.5, 2.0)


def _schedule_cleanup(root: Path) -> None:
    if os.name != "nt":
        shutil.rmtree(root, ignore_errors=True)
        return
    command = f'ping 127.0.0.1 -n 3 >nul & rmdir /s /q "{root}"'
    subprocess.Popen(
        ["cmd.exe", "/d", "/c", command],
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )


def _show_error(message: str) -> None:
    try:
        from tkinter import messagebox
        messagebox.showerror("LiveCatch updater", message)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--payload-dir", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.payload_dir.parent
    try:
        _wait_for_parent(args.pid)
        _install_with_retry(args.payload_dir, args.target_dir)
        executable = args.target_dir / "LiveCatch.exe"
        subprocess.Popen([str(executable)], cwd=str(args.target_dir), close_fds=True)
        _schedule_cleanup(root)
        return 0
    except Exception as exc:
        _show_error(f"Update failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
