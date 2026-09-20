"""Mixed watch-list GUI with opt-in Windows tray and login startup controls."""
from __future__ import annotations

import argparse
from queue import Empty
import sys
from time import monotonic
import tkinter as tk
from tkinter import messagebox, ttk

from .background import BackgroundConfig, BackgroundStore, InstanceLease, TrayController, WindowsStartup
from .channels import broadcast_key, manual_target, parse_targets
from .gui import LiveCatchApp
from .twitch_watch import WatchChannel
from .twitch_watch_gui import STATUS, TwitchWatchApp

STATUS.update({"upcoming": ("配信開始待ち", "Upcoming"), "duplicate": ("同じ配信を録画中", "Same broadcast recording")})


class DesktopApp(TwitchWatchApp):
    def __init__(self, *args, background_store=None, tray=None, startup=None, start_hidden=False, **kwargs):
        self.background_store = background_store or BackgroundStore()
        self._background_error = None
        try:
            self.background_config = self.background_store.load()
        except (OSError, ValueError) as exc:
            self.background_config = BackgroundConfig()
            self._background_error = str(exc)
        self.tray = tray or TrayController()
        self.startup = startup or WindowsStartup()
        self._hide_requested = False
        self._tray_deadline = None
        self._destroyed = False
        super().__init__(*args, **kwargs)
        if self._background_error:
            messagebox.showwarning("LiveCatch", self._background_error)
        if start_hidden or self.background_config.start_hidden:
            self.after(0, self._hide_to_tray)

    def _build(self):
        super()._build()
        self.notebook.tab(3, text=self._t("YouTube / Twitch 自動録画", "YouTube / Twitch auto-record"))
        # Existing Twitch registrations and settings remain in their original store.
        if not hasattr(self, "close_to_tray"):
            self.close_to_tray = tk.BooleanVar(value=self.background_config.close_to_tray)
            self.start_hidden = tk.BooleanVar(value=self.background_config.start_hidden)
            try:
                enabled = self.startup.enabled() if sys.platform == "win32" else False
            except OSError:
                enabled = False
            self.login_startup = tk.BooleanVar(value=enabled)
        frame = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(frame, text=self._t("バックグラウンド", "Background"))
        ttk.Label(frame, wraplength=900, text=self._t(
            "自動録画にはYouTubeのチャンネルURL・@handle、Twitchのチャンネル名・URLを登録できます。\n"
            "YouTubeの登録チャンネル一覧の自動インポートではありません。@handleはYouTubeとして扱います。",
            "Register YouTube channel URLs/@handles and Twitch names/URLs in auto-record.\n"
            "This does not import account subscriptions. Bare @handles mean YouTube.")).pack(anchor="w", pady=8)
        ttk.Checkbutton(frame, text=self._t("×ボタンで終了せずトレイに格納（Windows）", "Close button hides to tray (Windows)"),
                        variable=self.close_to_tray).pack(anchor="w", pady=4)
        ttk.Checkbutton(frame, text=self._t("次回はトレイに格納して起動", "Start hidden on next launch"),
                        variable=self.start_hidden).pack(anchor="w", pady=4)
        ttk.Checkbutton(frame, text=self._t("Windowsログイン時に起動", "Launch on Windows login"),
                        variable=self.login_startup, state="normal" if sys.platform == "win32" else "disabled").pack(anchor="w", pady=4)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=8)
        for ja, en, command in (("設定を適用", "Apply settings", self._apply_background),
                                ("今すぐトレイに格納", "Hide to tray", self._hide_to_tray),
                                ("終了", "Exit", self._request_exit)):
            ttk.Button(row, text=self._t(ja, en), command=command).pack(side="left", padx=(0, 8))
        ttk.Label(frame, wraplength=900, text=self._t(
            "自動で監視するには、自動録画タブの「アプリ起動時に監視再開」も有効にして保存してください。\n"
            "ウィンドウ非表示・画面オフでも動きますが、PCのスリープ・電源オフ・サインアウト中は動作しません。\n"
            "トレイから表示・監視停止・終了が可能です。トレイ起動に失敗した場合は画面を隠しません。",
            "Also save 'Monitor on app startup' in auto-record to begin monitoring automatically.\n"
            "Hidden windows/display-off work; sleep, shutdown and sign-out do not.\n"
            "Use the tray to open, pause or exit. Tray failure keeps/restores the window.")).pack(anchor="w", pady=8)

    def _watch_add(self):
        try:
            names = parse_targets(self.watch_input.get())
            existing = {c.login for c in self.watch_config.channels}
            channels = self.watch_config.channels + tuple(WatchChannel(n) for n in names if n not in existing)
            self._commit_watch_config(self._watch_values(channels))
            self.watch_input.set("")
        except (ValueError, OSError) as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _start(self):
        if self._watch_closing:
            return
        login = manual_target(self.vars["url"].get())
        active = self.watch_manager.recording_channels
        identities = {broadcast_key(name, self.watch_manager.states[name].stream_id) for name in active}
        if login is not None and login in active | identities:
            messagebox.showerror("LiveCatch", self._t("この配信は自動録画中です。", "This broadcast is already being recorded."))
            return
        was_active = self.supervisor.active
        self._manual_pending = login
        try:
            LiveCatchApp._start(self)
            if not was_active and self.supervisor.active:
                self._manual_login = login
        finally:
            self._manual_pending = None

    def _apply_background(self):
        try:
            config = BackgroundConfig(self.close_to_tray.get(), self.start_hidden.get())
            if sys.platform != "win32" and (config.close_to_tray or config.start_hidden):
                raise ValueError(self._t("トレイ常駐はWindows版で利用できます。", "Tray mode is available on Windows only."))
            self.background_store.save(config)
            self.background_config = config
            # Explicit Apply is the only code path that mutates login startup.
            if sys.platform == "win32":
                self.startup.set_enabled(self.login_startup.get())
            self._log(self._t("バックグラウンド設定を保存しました。", "Background settings saved."))
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _hide_to_tray(self):
        if self._watch_closing:
            return
        self._hide_requested = True
        if self.tray.ready.is_set():
            self.withdraw()
            return
        self._tray_deadline = monotonic() + 10
        try:
            self.tray.start(self.vars["language"].get())
        except Exception as exc:
            self._tray_failed(str(exc))

    def _restore(self):
        self._hide_requested = False
        self._tray_deadline = None
        self.deiconify()
        self.lift()

    def _tray_failed(self, message):
        self._restore()
        messagebox.showwarning("LiveCatch", self._t(
            "トレイを起動できないためウィンドウを表示します。\n",
            "Window restored because the tray is unavailable.\n") + message)

    def _drain_tray(self):
        for _ in range(30):
            try:
                command, detail = self.tray.commands.get_nowait()
            except Empty:
                break
            if command == "ready":
                self._tray_deadline = None
                if self._hide_requested and self.tray.ready.is_set():
                    self.withdraw()
            elif command == "failed":
                self._tray_failed(detail)
            elif command == "open":
                self._restore()
            elif command == "start":
                self._restore()  # Configuration or warnings must not open invisibly.
                self._watch_start()
            elif command == "pause":
                self.watch_manager.stop()
            elif command == "stop":
                self.watch_manager.shutdown()
            elif command == "exit":
                self._request_exit()
                if self._destroyed:
                    return
        if self._tray_deadline is not None and monotonic() >= self._tray_deadline:
            self._tray_failed("Tray startup timed out")

    def _poll(self):
        if self._destroyed:
            return
        super()._poll()
        if not self._destroyed:
            self._drain_tray()

    def _close(self):
        if self.background_config.close_to_tray and not self._watch_closing:
            self._hide_to_tray()
        else:
            self._request_exit()

    def _request_exit(self):
        self._restore()
        TwitchWatchApp._close(self)

    def destroy(self):
        if self._destroyed:
            return
        self._destroyed = True
        try:
            self.tray.stop()
        finally:
            super().destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="LiveCatch")
    parser.add_argument("--background", action="store_true", help="Hide to Windows tray after it is ready")
    args = parser.parse_args(argv)
    lease = InstanceLease()
    try:
        lease.acquire()
    except (OSError, RuntimeError) as exc:
        root = tk.Tk()
        root.withdraw()
        try:
            messagebox.showerror("LiveCatch", str(exc))
        finally:
            root.destroy()
        return 1
    try:
        DesktopApp(start_hidden=args.background).mainloop()
    finally:
        lease.release()
    return 0
