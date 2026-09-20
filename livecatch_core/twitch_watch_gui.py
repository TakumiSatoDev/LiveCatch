"""Twitch watch-list view layered on the existing recording GUI."""
from __future__ import annotations

from dataclasses import replace
import tkinter as tk
from tkinter import messagebox, ttk

from .config import Settings
from .gui import LiveCatchApp
from .tools import find_tool
from .twitch_watch import (
    MONITOR_PRESETS, WatchChannel, WatchConfig, WatchManager, WatchStore,
    apply_monitor_preset, normalize_channel, parse_channels,
)
from .ui_text import format_elapsed
from .updates import check_for_update

STATUS = {
    "idle": ("未開始", "Idle"), "waiting": ("監視待ち", "Waiting"),
    "disabled": ("無効", "Disabled"), "checking": ("配信を確認中", "Checking"),
    "offline": ("オフライン", "Offline"), "live": ("配信中", "Live"),
    "record_starting": ("録画開始準備中", "Preparing recording"),
    "record_extracting": ("配信情報取得中", "Extracting stream info"),
    "recording": ("録画・ダウンロード中", "Recording / downloading"),
    "postprocessing": ("結合・後処理中", "Muxing / post-processing"),
    "queued": ("録画枠待ち", "Waiting for slot"), "manual": ("手動で録画中", "Manual recording"),
    "check_error": ("確認エラー・再試行待ち", "Check error / backoff"),
    "retry_wait": ("録画の再試行待ち", "Recording retry wait"),
    "retry_limit": ("再試行上限・次の配信待ち", "Retry limit / next broadcast"),
    "completed": ("録画完了", "Completed"), "skipped": ("今回の配信は終了・停止済み", "Broadcast completed / skipped"),
    "stopping": ("停止処理中", "Stopping"), "paused": ("監視停止", "Paused"),
}


class TwitchWatchApp(LiveCatchApp):
    def __init__(self, store=None, update_checker=check_for_update, watch_store=None, manager=None):
        self.watch_store = watch_store or WatchStore()
        self._watch_load_error = None
        try:
            self.watch_config = self.watch_store.load()
        except (ValueError, OSError) as exc:
            self.watch_config = WatchConfig()
            self._watch_load_error = str(exc)
        self._manual_login = None
        self._manual_pending = None
        self._watch_closing = False
        self.watch_manager = manager or WatchManager(manual_channels=self._manual_channels)
        self.watch_manager.configure(self.watch_config, Settings())
        super().__init__(store=store, update_checker=update_checker)
        self.watch_manager.configure(
            self.watch_config, apply_monitor_preset(self.settings(), self.watch_config.monitor_preset))
        if self._watch_load_error:
            messagebox.showwarning("LiveCatch", self._t(
                "自動録画設定を読み込めません。元ファイルは変更しません。\n",
                "Cannot read watch settings; the original file was not changed.\n") + self._watch_load_error)
        elif self.watch_config.autostart:
            self.after(0, self._watch_start)

    def _manual_channels(self):
        channels = {self._manual_login} if self._manual_login and self.supervisor.active else set()
        if self._manual_pending:
            channels.add(self._manual_pending)
        return channels

    def _build(self):
        super()._build()
        if not hasattr(self, "watch_input"):
            self.watch_input = tk.StringVar()
            self.watch_interval = tk.StringVar(value=str(self.watch_config.interval))
            self.watch_limit = tk.StringVar(value=str(self.watch_config.max_recordings))
            self.watch_auto = tk.BooleanVar(value=self.watch_config.autostart)
            self.watch_preset = tk.StringVar(value=self.watch_config.monitor_preset)
            self.watch_catchup = tk.StringVar(value=self.watch_config.catchup_mode)
        frame = ttk.Frame(self.notebook, padding=10)
        self.watch_tab = frame
        self.notebook.add(frame, text=self._t("YouTube / Twitch 自動録画", "YouTube / Twitch auto-record"))
        ttk.Label(frame, text=self._t("チャンネル名 / URL（空白・カンマで複数登録）",
                                     "Channel names / URLs (space/comma separated)")).pack(anchor="w")
        add = ttk.Frame(frame)
        add.pack(fill="x", pady=(4, 6))
        ttk.Entry(add, textvariable=self.watch_input).pack(side="left", fill="x", expand=True)
        ttk.Button(add, text=self._t("追加", "Add"), command=self._watch_add).pack(side="left", padx=4)
        tree_box = ttk.Frame(frame)
        tree_box.pack(fill="x")
        self.watch_tree = ttk.Treeview(
            tree_box, columns=("enabled", "status", "progress", "elapsed", "checked", "title"),
            height=6, selectmode="extended")
        self.watch_tree.heading("#0", text=self._t("チャンネル", "Channel"))
        self.watch_tree.column("#0", width=145, stretch=False)
        for key, title, width in (("enabled", ("監視", "Enabled"), 55),
                                  ("status", ("状態", "Status"), 175),
                                  ("progress", ("進捗", "Progress"), 190),
                                  ("elapsed", ("時間", "Time"), 70),
                                  ("checked", ("最終確認", "Last check"), 75),
                                  ("title", ("配信タイトル", "Stream title"), 220)):
            self.watch_tree.heading(key, text=self._t(*title))
            self.watch_tree.column(key, width=width, stretch=key == "title")
        scroll = ttk.Scrollbar(tree_box, orient="vertical", command=self.watch_tree.yview)
        scroll.pack(side="right", fill="y")
        self.watch_tree.configure(yscrollcommand=scroll.set)
        self.watch_tree.pack(side="left", fill="both", expand=True)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=5)
        for title, command in ((("有効 / 無効", "Enable / disable"), self._watch_toggle),
                               (("削除", "Remove"), self._watch_remove),
                               (("選択の録画停止", "Stop selected recording"), self._watch_selected_stop),
                               (("選択の強制停止", "Force stop selected"), lambda: self._watch_selected_stop(force=True)),
                               (("選択を再試行", "Retry selected"), self._watch_retry)):
            ttk.Button(row, text=self._t(*title), command=command).pack(side="left", padx=(0, 4))
        limits = ttk.Frame(frame)
        limits.pack(fill="x", pady=3)
        ttk.Label(limits, text=self._t("確認間隔（秒）", "Check interval (s)")).pack(side="left")
        ttk.Spinbox(limits, from_=30, to=3600, textvariable=self.watch_interval, width=7).pack(side="left", padx=(4, 12))
        ttk.Label(limits, text=self._t("同時自動録画上限", "Max auto-recordings")).pack(side="left")
        ttk.Combobox(limits, textvariable=self.watch_limit, values=tuple(range(1, 9)), state="readonly", width=4).pack(side="left", padx=4)
        ttk.Checkbutton(limits, text=self._t("アプリ起動時に監視再開", "Monitor on app startup"), variable=self.watch_auto).pack(side="left", padx=8)

        monitor_opts = ttk.Frame(frame)
        monitor_opts.pack(fill="x", pady=3)
        ttk.Label(monitor_opts, text=self._t("監視録画プリセット", "Monitoring preset")).pack(side="left")
        ttk.Combobox(
            monitor_opts, textvariable=self.watch_preset, values=tuple(MONITOR_PRESETS),
            state="readonly", width=12).pack(side="left", padx=(4, 14))
        ttk.Label(monitor_opts, text=self._t("配信途中で検知した場合", "When detected mid-stream")).pack(side="left")
        ttk.Combobox(
            monitor_opts, textvariable=self.watch_catchup,
            values=("from_start", "live_edge"), state="readonly", width=12).pack(side="left", padx=4)

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=4)
        for title, command in ((("設定保存", "Save settings"), self._watch_save),
                               (("監視開始", "Start monitoring"), self._watch_start),
                               (("監視だけ停止", "Pause monitoring only"), self.watch_manager.stop),
                               (("監視・全自動録画停止", "Stop monitoring and all auto-recordings"), self.watch_manager.shutdown)):
            ttk.Button(actions, text=self._t(*title), command=command).pack(side="left", padx=(0, 4))
        self.watch_summary = tk.StringVar()
        ttk.Label(frame, textvariable=self.watch_summary).pack(anchor="w", pady=3)
        self.watch_detail = tk.StringVar()
        ttk.Label(frame, textvariable=self.watch_detail, wraplength=900).pack(anchor="w")
        ttk.Label(frame, wraplength=900, text=self._t(
            "監視中でもチャンネルを追加でき、追加直後に現在の配信を確認します。from_startはDVR等で利用可能な範囲を先頭から追いつきます。\n"
            "保存先・画質・Cookie等は共通設定、fragment/prefetchは監視録画プリセットを使用します。",
            "Channels can be added while monitoring and are checked immediately. from_start catches up from the available DVR/start when supported.\n"
            "Output/quality/cookies use shared settings; fragment/prefetch use the monitoring preset.")).pack(anchor="w", pady=(4, 0))
        self.notebook.bind("<<NotebookTabChanged>>", self._watch_tab_changed, add="+")
        self.after_idle(self._watch_tab_changed)
        self._render_watch()

    def _watch_tab_changed(self, _event=None):
        if not hasattr(self, "watch_tab"):
            return
        self._set_manual_controls_visible(self.notebook.select() != str(self.watch_tab))

    def _watch_progress_text(self, state) -> str:
        if state is None:
            return ""
        if state.recording is None:
            return "100%" if state.status == "completed" else ""
        progress = list(state.progress.values())
        percentages = [float(p["percent"]) for p in progress
                       if isinstance(p.get("percent"), (int, float))]
        fragments = [(p.get("fragment_index"), p.get("fragment_count")) for p in progress]
        parts = []
        if percentages:
            parts.append(f"{min(percentages):.0f}%")
        known = [(cur, total) for cur, total in fragments
                 if isinstance(cur, int) and isinstance(total, int) and total > 0]
        if known:
            cur, total = min(known, key=lambda pair: pair[0] / pair[1])
            parts.append(f"{cur}/{total} frag")
        elif fragments:
            current = [cur for cur, _total in fragments if isinstance(cur, int)]
            if current:
                parts.append(f"frag {max(current)}")
        speeds = [float(p["speed"]) for p in progress
                  if isinstance(p.get("speed"), (int, float)) and p["speed"] > 0]
        if speeds:
            parts.append(f"{self._format_bytes(sum(speeds))}/s")
        if parts:
            return " · ".join(parts)
        return self._t(
            "処理中…" if state.phase in {"starting", "extracting", "postprocessing"} else "録画中…",
            "Working…" if state.phase in {"starting", "extracting", "postprocessing"} else "Recording…",
        )

    def _watch_values(self, channels=None):
        return WatchConfig(
            self.watch_config.channels if channels is None else tuple(channels),
            int(self.watch_interval.get()), int(self.watch_limit.get()), bool(self.watch_auto.get()),
            self.watch_preset.get(), self.watch_catchup.get(),
        )

    def _commit_watch_config(self, config):
        config.validate()
        settings = self.settings()
        manager = self.watch_manager
        if self._watch_closing or manager.running or any(s.probe for s in manager.states.values()):
            raise ValueError(self._t("監視を停止し、確認処理の終了後に編集してください。", "Pause monitoring and wait for checks to stop before editing."))
        if manager.recording_channels - {c.login for c in config.channels}:
            raise ValueError(self._t("削除するチャンネルの録画を先に停止してください。", "Stop a channel's recording before removing it."))
        self.watch_store.save(config)
        manager.configure(config, apply_monitor_preset(settings, config.monitor_preset))
        self.watch_config = config
        self._render_watch()

    def _watch_add(self):
        try:
            names = parse_channels(self.watch_input.get())
            existing = {c.login for c in self.watch_config.channels}
            additions = tuple(WatchChannel(n) for n in names if n not in existing)
            if not additions:
                self.watch_input.set("")
                return
            channels = self.watch_config.channels + additions
            if self.watch_manager.running:
                config = replace(self.watch_config, channels=channels)
                config.validate()
                self.watch_store.save(config)
                monitor_settings = apply_monitor_preset(self.settings(), config.monitor_preset)
                self.watch_manager.add_channels(additions, monitor_settings)
                self.watch_config = config
                self._render_watch()
            else:
                self._commit_watch_config(self._watch_values(channels))
            self.watch_input.set("")
        except (ValueError, OSError) as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _watch_toggle(self):
        try:
            selected = set(self.watch_tree.selection())
            channels = [replace(c, enabled=not c.enabled) if c.login in selected else c for c in self.watch_config.channels]
            self._commit_watch_config(self._watch_values(channels))
        except (ValueError, OSError) as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _watch_remove(self):
        try:
            selected = set(self.watch_tree.selection())
            self._commit_watch_config(self._watch_values(c for c in self.watch_config.channels if c.login not in selected))
        except (ValueError, OSError) as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _watch_save(self):
        try:
            self._commit_watch_config(self._watch_values())
        except (ValueError, OSError) as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _watch_start(self):
        if self._watch_closing or self.watch_manager.running:
            return
        try:
            if not find_tool("ffmpeg") or not find_tool("ffprobe"):
                raise ValueError(self._t("ffmpegとffprobeを用意してください。", "ffmpeg and ffprobe are required."))
            self._commit_watch_config(self._watch_values())
            base_settings = self.settings()
            settings = apply_monitor_preset(base_settings, self.watch_config.monitor_preset)
            if (settings.concurrent_fragments > 32 or settings.prefetch > 4) and not messagebox.askyesno("LiveCatch", self._t(
                "高負荷設定は同時録画本数ぶん適用されます。監視を開始しますか？",
                "High-load settings apply to EACH concurrent recording. Start monitoring?")):
                return
            self.store.save(base_settings)
            self.watch_manager.configure(self.watch_config, settings)
            self.watch_manager.start()
            self._render_watch()
        except (ValueError, OSError) as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _watch_selected_stop(self, *, force=False):
        if force and not messagebox.askyesno("LiveCatch", self._t(
                "未完成ファイルが残る場合があります。選択した自動録画を強制停止しますか？",
                "Partial files may remain. Force-stop the selected auto-recordings?")):
            return
        for login in self.watch_tree.selection():
            self.watch_manager.stop_recording(login, force=force)
        self._render_watch()

    def _watch_retry(self):
        try:
            for login in self.watch_tree.selection():
                self.watch_manager.retry_channel(login)
            self._render_watch()
        except ValueError as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _render_watch(self):
        if not hasattr(self, "watch_tree"):
            return
        manager = self.watch_manager
        desired = {c.login for c in self.watch_config.channels}
        for login in self.watch_tree.get_children():
            if login not in desired:
                self.watch_tree.delete(login)
        for c in self.watch_config.channels:
            state = manager.states.get(c.login)
            status = state.status if state else "idle"
            if not c.enabled and not (state and state.recording):
                status = "disabled"
            elapsed = format_elapsed(manager.elapsed_seconds(c.login)) if state and (
                state.recording is not None or state.last_elapsed > 0
            ) else ""
            progress = self._watch_progress_text(state)
            values = (self._t("有効", "Yes") if c.enabled else self._t("無効", "No"),
                      self._t(*STATUS.get(status, (status, status))), progress, elapsed,
                      state.checked if state else "", state.title if state else "")
            if not self.watch_tree.exists(c.login):
                self.watch_tree.insert("", "end", iid=c.login, text=c.login, values=values)
            elif tuple(self.watch_tree.item(c.login, "values")) != values:
                self.watch_tree.item(c.login, values=values)
        mode = self._t("監視中", "Monitoring") if manager.running else self._t("監視停止", "Paused")
        self.watch_summary.set(self._t(
            f"{mode} / 登録 {len(desired)}件 / 自動録画 {len(manager.recording_channels)}/{manager.config.max_recordings}件 / {manager.config.monitor_preset} / {manager.config.catchup_mode}",
            f"{mode} / {len(desired)} channels / auto-recordings {len(manager.recording_channels)}/{manager.config.max_recordings} / {manager.config.monitor_preset} / {manager.config.catchup_mode}"))
        selected = self.watch_tree.selection()
        if selected and selected[0] in manager.states:
            state = manager.states[selected[0]]
            detail = state.detail
            progress = self._watch_progress_text(state)
            self.watch_detail.set(" / ".join(part for part in (progress, detail) if part))
        else:
            self.watch_detail.set("")

    def _start(self):
        if self._watch_closing:
            return
        try:
            login = normalize_channel(self.vars["url"].get())
        except ValueError:
            login = None
        if login in self.watch_manager.recording_channels:
            messagebox.showerror("LiveCatch", self._t("このチャンネルは自動録画中です。", "This channel is already being auto-recorded."))
            return
        was_active = self.supervisor.active
        # Tk dialogs run a nested event loop: reserve the channel before the
        # base Start handler opens the high-load confirmation dialog.
        self._manual_pending = login
        try:
            super()._start()
            if not was_active and self.supervisor.active:
                self._manual_login = login
        finally:
            self._manual_pending = None

    def _poll(self):
        super()._poll()
        self.watch_manager.tick()
        for event in self.watch_manager.events.drain():
            self._log(event["message"])
        self._render_watch()
        if self._watch_closing and not self.supervisor.active and not self.watch_manager.has_children:
            self.destroy()

    def _close(self):
        if self.supervisor.active or self.watch_manager.has_children:
            if messagebox.askyesno("LiveCatch", self._t(
                    "監視と全録画を停止し、結合・終了処理が終わってから閉じますか？",
                    "Stop monitoring and all recordings, then close after finalization?")):
                self._watch_closing = True
                self.watch_manager.shutdown()
                self.supervisor.stop()
                self.start_button.configure(state="disabled")
            return
        self.watch_manager.stop()
        self.destroy()


def main():
    TwitchWatchApp().mainloop()
