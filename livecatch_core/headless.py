"""Foreground, GUI-free monitor for a terminal or an external service manager."""
from __future__ import annotations

import argparse
from pathlib import Path
import signal
import sys
import time

from .background import InstanceLease
from .config import ConfigStore, CONFIG_FILE
from .events import Emitter
from .tools import find_tool
from .twitch_watch import WatchManager, WatchStore, WATCH_FILE, apply_monitor_preset


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m livecatch_core watch")
    parser.add_argument("--config", type=Path, default=CONFIG_FILE)
    parser.add_argument("--watch-config", type=Path, default=WATCH_FILE)
    parser.add_argument("--allow-extreme", action="store_true", help="Acknowledge per-recording high-load settings")
    args = parser.parse_args(argv)
    emit = Emitter(sys.stdout)
    manager = WatchManager()
    signals = []
    previous = {}
    lease = InstanceLease()
    try:
        base_settings, config = ConfigStore(args.config).load(), WatchStore(args.watch_config).load()
        settings = apply_monitor_preset(base_settings, config.monitor_preset)
        if not find_tool("ffmpeg") or not find_tool("ffprobe"):
            raise ValueError("ffmpeg and ffprobe are required")
        extreme = settings.concurrent_fragments > 32 or settings.prefetch > 4
        if extreme and not args.allow_extreme:
            raise ValueError("High-load settings require --allow-extreme for headless monitoring")
        manager.configure(config, settings)
        lease.acquire()
        def request_stop(number, _frame):
            signals.append(number)
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, request_stop)
        manager.start()
        emit("log", message="Headless monitoring started. First signal stops gracefully; second forces workers to stop.")
        handled = 0
        while manager.running or manager.has_children:
            if len(signals) > handled:
                manager.shutdown(force=len(signals) > 1)
                handled = len(signals)
            manager.tick()
            for event in manager.events.drain():
                emit("log", message=event["message"])
            time.sleep(0.1)
        return 130 if signals else 0
    except Exception as exc:
        emit("error", message=f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        try:
            if manager.running or manager.has_children:
                manager.shutdown(force=True)
                # Reap children before releasing the shared instance lock.
                end = time.monotonic() + 15
                while manager.has_children and time.monotonic() < end:
                    manager.tick()
                    time.sleep(0.05)
        finally:
            lease.release()
