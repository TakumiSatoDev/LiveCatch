"""Data-driven Tk view. All Tk access is confined to the main thread."""
from __future__ import annotations

from dataclasses import fields
import json
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser
from threading import Thread
from time import monotonic

from . import __version__
from .config import BROWSERS, QUALITY, ConfigStore, Settings
from .options import ydl_options
from .supervisor import Supervisor
from .tools import find_tool
from .updates import UpdateCheck, check_for_update, download_update
from .ui_text import configure_tk_utf8, configure_windows_utf8, format_elapsed, repair_mojibake

UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000

PHASE_LABELS = {
    "starting": ("開始準備中", "Preparing"),
    "extracting": ("配信情報を取得中", "Extracting stream info"),
    "downloading": ("録画・ダウンロード中", "Recording / downloading"),
    "postprocessing": ("結合・後処理中", "Muxing / post-processing"),
    "done": ("完了", "Completed"),
}
PROGRESS_PHASES = ("starting", "extracting", "downloading", "postprocessing", "done")
PROGRESS_COLORS = {
    "idle": ("#e9eef2", "#52606d"),
    "active": ("#1769aa", "#ffffff"),
    "done": ("#2e7d32", "#ffffff"),
    "error": ("#b3261e", "#ffffff"),
}

LABELS = {
    "mode": ("録画モード", "Recording mode"), "url": ("YouTube / Twitch URL", "YouTube / Twitch URL"),
    "save_dir": ("保存先", "Save folder"), "use_temp_dir": ("高速一時保存先を使う", "Use temp folder"),
    "temp_dir": ("一時保存先", "Temp folder"), "wait_seconds": ("予約確認間隔（秒）", "Reservation interval (seconds)"),
    "cookies_from_browser": ("ブラウザCookieを使う", "Use browser cookies"), "browser": ("ブラウザ", "Browser"),
    "live_from_start": ("予約録画でも先頭から取得", "Reservation: try from start"),
    "write_info_json": ("info.jsonを書き出す", "Write info.json"), "embed_metadata": ("メタデータを埋め込む", "Embed metadata"),
    "lightweight_catchup_postprocess": ("現在まで取得ではメタデータ省略", "Snapshot: skip metadata"),
    "quality_preset": ("画質", "Quality"), "output_format": ("保存形式", "Container"),
    "concurrent_fragments": ("同時fragment数（1〜256）", "Fragment concurrency (1–256)"),
    "output_template": ("出力テンプレート", "Output template"), "engine": ("取得エンジン", "Download engine"),
    "prefetch": ("先読み上限＝並列数×倍率", "Prefetch window multiplier"),
    "gpu_export": ("取得後に別ファイルへ変換", "Separate export after recording"),
    "gpu_device": ("GPU番号", "GPU device"), "export_height": ("出力の高さ（0＝元サイズ）", "Export height (0 = original)"),
    "gpu_jobs": ("同時変換数", "Concurrent exports"),
    "gpu_preset": ("NVENC速度プリセット", "NVENC speed preset"),
}
CHOICES = {"mode": ("reservation", "live_full", "catchup_stop"), "browser": BROWSERS,
           "quality_preset": tuple(QUALITY), "output_format": ("mp4", "mkv", "webm"),
           "engine": ("bounded", "stock"), "gpu_export": ("off", "auto", "cuda", "cpu"),
           "concurrent_fragments": (1, 2, 4, 8, 16, 32, 64, 96, 128, 192, 256),
           "prefetch": (1, 2, 3, 4, 5, 6, 7, 8),
           "export_height": (0, 360, 480, 720, 1080, 1440, 2160),
           "gpu_jobs": (1, 2, 3, 4, 5, 6, 7, 8),
           "gpu_preset": ("balanced", "fast", "max_speed")}


class LiveCatchApp(tk.Tk):
    def __init__(self, store: ConfigStore | None = None, update_checker=check_for_update):
        configure_windows_utf8()
        super().__init__()
        configure_tk_utf8(self)
        self.title(f"LiveCatch {__version__}")
        self.geometry("1040x820")
        self.minsize(840, 680)
        self.store = store or ConfigStore()
        try:
            settings = self.store.load()
        except (ValueError, OSError) as exc:
            settings = Settings()
            messagebox.showwarning("LiveCatch", f"設定を読み込めません。元ファイルは変更しません。\n{exc}")
        self.supervisor = Supervisor()
        self.vars = {f.name: (tk.BooleanVar(value=getattr(settings, f.name)) if type(getattr(settings, f.name)) is bool
                             else tk.StringVar(value=str(getattr(settings, f.name)))) for f in fields(Settings)}
        self.closing = False
        self._progress = {
            "phase": "starting", "active": False, "streams": {}, "detail": "",
            "export_current": 0, "export_total": 0, "outputs": 0,
            "started_at": None, "elapsed": None,
        }
        self._update_checker = update_checker
        self._update_info: UpdateCheck | None = None
        self._update_checking = False
        self._update_installing = False
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._poll)
        self._check_updates()
        self.after(UPDATE_CHECK_INTERVAL_MS, self._scheduled_update_check)

    def _t(self, ja, en):
        value = en if self.vars["language"].get() == "en" else ja
        return repair_mojibake(value)

    def _build(self):
        self.body = ttk.Frame(self, padding=14)
        self.body.pack(fill="both", expand=True)
        header = ttk.Frame(self.body)
        header.pack(fill="x")
        ttk.Label(header, text=f"LiveCatch {__version__}", font=("", 20, "bold")).pack(side="left")
        self.update_var = tk.StringVar(value=self._t("更新を確認中…", "Checking for updates…"))
        ttk.Label(header, textvariable=self.update_var).pack(side="right", padx=(8, 0))
        self.update_action_var = tk.StringVar(value=self._t("更新を確認", "Check for updates"))
        self.update_button = ttk.Button(header, textvariable=self.update_action_var, command=self._open_update)
        self.update_button.pack(side="right")
        self.update_button.configure(state="disabled" if self._update_checking else "normal")
        if self._update_info is not None:
            self._apply_update_result(self._update_info)
        lang = ttk.Combobox(header, textvariable=self.vars["language"], values=("ja", "en"), state="readonly", width=5)
        lang.pack(side="right")
        lang.bind("<<ComboboxSelected>>", self._rebuild)
        self.notebook = ttk.Notebook(self.body)
        self.notebook.pack(fill="x", pady=12)

        def add_fields(parent, names):
            parent.columnconfigure(1, weight=1)
            for row, name in enumerate(names):
                label = self._t(*LABELS[name])
                var = self.vars[name]
                if isinstance(var, tk.BooleanVar):
                    ttk.Checkbutton(parent, text=label, variable=var).grid(
                        row=row, column=0, columnspan=3, sticky="w", pady=3)
                    continue
                ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
                if name in CHOICES:
                    widget = ttk.Combobox(parent, textvariable=var, values=CHOICES[name], state="readonly")
                else:
                    widget = ttk.Entry(parent, textvariable=var)
                widget.grid(row=row, column=1, sticky="ew", pady=3)
                if name in ("save_dir", "temp_dir"):
                    ttk.Button(parent, text="...", width=3,
                               command=lambda n=name: self._browse(n)).grid(row=row, column=2, padx=(4, 0))

        self.record_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.record_tab, text=self._t("録画", "Recording"))
        add_fields(self.record_tab, ("mode", "url"))

        self.settings_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.settings_tab, text=self._t("設定", "Settings"))
        self.settings_tab.columnconfigure(0, weight=1)
        self.settings_tab.columnconfigure(1, weight=1)
        setting_groups = (
            (("保存・画質", "Output / quality"),
             ("save_dir", "use_temp_dir", "temp_dir", "quality_preset", "output_format",
              "output_template", "write_info_json", "embed_metadata")),
            (("取得・手動録画", "Acquisition / manual recording"),
             ("wait_seconds", "live_from_start", "lightweight_catchup_postprocess",
              "cookies_from_browser", "browser", "engine", "concurrent_fragments", "prefetch")),
        )
        for index, (title, names) in enumerate(setting_groups):
            frame = ttk.LabelFrame(self.settings_tab, text=self._t(*title), padding=8)
            frame.grid(row=0, column=index, sticky="nsew", padx=4, pady=4)
            add_fields(frame, names)
        self.manual_help = ttk.Label(self.body, wraplength=960, text=self._t(
            "reservation＝予約 / live_full＝終了まで / catchup_stop＝最初に観測した共通地点まで（YouTube DVR）。\n"
            "録画は配信の映像・音声を再エンコードせず保存します。",
            "reservation = wait / live_full = until end / catchup_stop = shared initial cutoff (YouTube DVR).\n"
            "Recordings are saved without an extra re-encode step."))
        self.manual_help.pack(anchor="w", pady=(0, 8))
        bar = ttk.Frame(self.body)
        self.manual_bar = bar
        bar.pack(fill="x")
        self.start_button = ttk.Button(bar, text=self._t("開始", "Start"), command=self._start)
        self.start_button.pack(side="left")
        ttk.Button(bar, text=self._t("停止", "Stop"), command=self.supervisor.stop).pack(side="left", padx=5)
        ttk.Button(bar, text=self._t("強制停止", "Force stop"), command=self._force).pack(side="left")
        ttk.Button(bar, text=self._t("設定・実行内容", "Execution settings"), command=self._preview).pack(side="left", padx=5)
        ttk.Button(bar, text=self._t("ツール確認", "Tools check"), command=self._diagnose).pack(side="left")
        ttk.Button(bar, text=self._t("保存先を開く", "Open folder"), command=self._open_folder).pack(side="left", padx=5)
        bar.pack_configure(pady=(0, 8))
        status = ttk.LabelFrame(self.body, text=self._t("進行状況", "Progress"), padding=8)
        self.manual_status = status
        status.pack(fill="x", pady=(0, 8))
        status.columnconfigure(0, weight=1)
        status.columnconfigure(1, weight=1)
        ttk.Label(status, text=self._t("状態", "Status")).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.phase_var = tk.StringVar()
        ttk.Label(status, textvariable=self.phase_var).grid(row=0, column=1, sticky="w")
        pipeline = tk.Frame(status, bd=0, highlightthickness=0)
        pipeline.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(7, 3))
        self.phase_steps = {}
        for index, phase in enumerate(PROGRESS_PHASES):
            if index:
                tk.Label(pipeline, text="›", width=2, fg="#7b8794").pack(side="left")
            step = tk.Label(pipeline, text=self._phase_text(phase), anchor="center", width=14,
                            padx=5, pady=5, relief="ridge", bd=1, font=("", 9, "bold"))
            step.pack(side="left", fill="x", expand=True)
            self.phase_steps[phase] = step
        meter = ttk.Frame(status)
        meter.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(3, 2))
        meter.columnconfigure(0, weight=1)
        self.progressbar = ttk.Progressbar(meter, mode="indeterminate", maximum=100)
        self.progressbar.grid(row=0, column=0, sticky="ew")
        self.percent_var = tk.StringVar()
        ttk.Label(meter, textvariable=self.percent_var, width=8, anchor="e").grid(row=0, column=1, padx=(8, 0))
        self.metrics_var = tk.StringVar()
        ttk.Label(status, textvariable=self.metrics_var, wraplength=960).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.detail_var = tk.StringVar()
        ttk.Label(status, textvariable=self.detail_var, wraplength=960).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self._render_progress()
        self.log = tk.Text(self.body, height=16, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True, pady=(12, 0))
        self.start_button.configure(state="disabled" if self.supervisor.active else "normal")

    def _set_manual_controls_visible(self, visible: bool):
        if not all(hasattr(self, name) for name in ("manual_help", "manual_bar", "manual_status", "log")):
            return
        widgets = (
            (self.manual_help, {"anchor": "w", "pady": (0, 8)}),
            (self.manual_bar, {"fill": "x", "pady": (0, 8)}),
            (self.manual_status, {"fill": "x", "pady": (0, 8)}),
        )
        if visible:
            for widget, options in widgets:
                if not widget.winfo_manager():
                    widget.pack(before=self.log, **options)
        else:
            for widget, _options in widgets:
                if widget.winfo_manager():
                    widget.pack_forget()

    def _rebuild(self, _event=None):
        text = self.log.get("1.0", "end-1c")
        self.body.destroy()
        self._build()
        self._log(text)

    def _browse(self, name):
        value = filedialog.askdirectory()
        if value:
            self.vars[name].set(value)

    def settings(self) -> Settings:
        data = {}
        defaults = Settings()
        for name, var in self.vars.items():
            value = var.get()
            data[name] = int(value) if type(getattr(defaults, name)) is int else value
        return Settings.from_dict(data)

    def _start(self):
        try:
            settings = self.settings()
            settings.validate()
            extreme = (
                settings.concurrent_fragments > 32
                or settings.prefetch > 4
            )
            if extreme and not messagebox.askyesno("LiveCatch", self._t(
                "高負荷の実験設定です。回線・ディスク・CPU負荷が大きくなり、"
                "YouTube/Twitch側の429・タイムアウトや、逆に低速化する場合があります。続行しますか？",
                "Experimental high-load settings are enabled. Network, disk and CPU load may spike; "
                "the service may throttle with 429/timeouts and performance can get worse. Continue?"
            )):
                return
            self.store.save(settings)
            self._reset_progress()
            self.supervisor.start(settings)
            self._progress["active"] = True
            self._progress["started_at"] = monotonic()
            self._progress["elapsed"] = None
            self._render_progress()
            self.start_button.configure(state="disabled")
        except Exception as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _preview(self):
        try:
            settings = self.settings()
            options = ydl_options(settings, find_tool("ffmpeg"))
            options.pop("retry_sleep_functions", None)
            self._log(json.dumps({"engine": settings.engine, "yt_dlp": options},
                                 ensure_ascii=False, indent=2))
        except Exception as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _diagnose(self):
        def work():
            try:
                from yt_dlp.version import __version__ as version
            except ImportError:
                version = "missing: install requirements.txt"
            self.supervisor.events.put({"event": "log", "message": json.dumps(
                {"yt_dlp": version, "ffmpeg": find_tool("ffmpeg"),
                 "ffprobe": find_tool("ffprobe"), "deno": find_tool("deno")},
                ensure_ascii=False)})
        Thread(target=work, daemon=True).start()

    def _open_folder(self):
        try:
            folder = Path(self.vars["save_dir"].get()).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(folder))
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
        except OSError as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _force(self):
        if self.supervisor.active and messagebox.askyesno("LiveCatch", self._t(
            "処理中の録画・結合を中断します。未完成ファイルが残る場合があります。強制停止しますか？",
            "This interrupts recording/muxing and may leave partial files. Force stop?")):
            try:
                self.supervisor.force_stop()
            except OSError as exc:
                messagebox.showerror("LiveCatch", str(exc))

    def _close(self):
        if self.supervisor.active:
            if messagebox.askyesno("LiveCatch", self._t("停止処理が完了してから閉じますか？", "Stop and close after finalization?")):
                self.closing = True
                self.supervisor.stop()
            return
        self.destroy()

    def _check_updates(self):
        if self._update_checking or self._update_installing:
            return
        self._update_checking = True
        self.update_var.set(self._t("更新を確認中…", "Checking for updates…"))
        self.update_action_var.set(self._t("確認中…", "Checking…"))
        self.update_button.configure(state="disabled")

        def work():
            try:
                result = self._update_checker(__version__)
            except Exception:
                result = None
            try:
                self.after(0, lambda: self._finish_update_check(result))
            except RuntimeError:
                pass

        Thread(target=work, daemon=True, name="lc-update-check").start()

    def _finish_update_check(self, result: UpdateCheck | None):
        self._update_checking = False
        self._apply_update_result(result)
        self.update_button.configure(state="normal")

    def _apply_update_result(self, result: UpdateCheck | None):
        self._update_info = result
        if result is None:
            self.update_var.set(self._t("更新を確認できません", "Update check unavailable"))
            self.update_action_var.set(self._t("再確認", "Retry"))
        elif result.update_available:
            self.update_var.set(self._t(
                f"更新版があります v{result.latest_version}",
                f"Update available v{result.latest_version}"))
            self.update_action_var.set(self._t(
                "更新する" if result.download_url else "詳細を開く",
                "Update now" if result.download_url else "Open release"))
        else:
            self.update_var.set(self._t(
                f"最新版です v{result.latest_version}",
                f"Up to date v{result.latest_version}"))
            self.update_action_var.set(self._t("更新を確認", "Check for updates"))

    def _open_update(self):
        if self._update_checking or self._update_installing:
            return
        if self._update_info and self._update_info.update_available:
            if not self._update_info.download_url or not getattr(sys, "frozen", False):
                webbrowser.open(self._update_info.url)
                return
            if self.supervisor.active:
                messagebox.showwarning(
                    "LiveCatch",
                    self._t("録画中は更新できません。録画を終了してから実行してください。",
                            "Updates are unavailable while recording. Finish the recording first."),
                )
                return
            if messagebox.askyesno(
                "LiveCatch",
                self._t(
                    f"v{self._update_info.latest_version}へ更新します。アプリを終了して更新後に再起動しますか？",
                    f"Update to v{self._update_info.latest_version} and restart LiveCatch now?",
                ),
            ):
                self._start_self_update()
        else:
            self._check_updates()

    def _scheduled_update_check(self):
        if not self.closing and not self._update_installing:
            self._check_updates()
        if self.winfo_exists():
            self.after(UPDATE_CHECK_INTERVAL_MS, self._scheduled_update_check)

    def _start_self_update(self):
        update = self._update_info
        if not update or not update.download_url:
            return
        self._update_installing = True
        self.update_var.set(self._t("更新ファイルを取得中…", "Downloading update…"))
        self.update_action_var.set(self._t("更新中…", "Updating…"))
        self.update_button.configure(state="disabled")

        def work():
            try:
                payload = download_update(update)
                result = (payload, None)
            except Exception as exc:
                result = (None, exc)
            try:
                self.after(0, lambda: self._finish_self_update(*result))
            except RuntimeError:
                pass

        Thread(target=work, daemon=True, name="lc-self-update").start()

    def _finish_self_update(self, payload: Path | None, error: Exception | None):
        self._update_installing = False
        if error is not None or payload is None:
            self.update_var.set(self._t("更新に失敗しました", "Update failed"))
            self.update_action_var.set(self._t("再試行", "Retry"))
            self.update_button.configure(state="normal")
            messagebox.showerror("LiveCatch", str(error) if error else "Update package is missing")
            return
        updater = payload / "LiveCatchUpdater.exe"
        target = Path(sys.executable).resolve().parent
        try:
            subprocess.Popen(
                [str(updater), "--pid", str(os.getpid()), "--payload-dir", str(payload),
                 "--target-dir", str(target)],
                cwd=str(payload),
                close_fds=True,
            )
        except OSError as exc:
            self.update_var.set(self._t("更新を開始できません", "Could not start updater"))
            self.update_action_var.set(self._t("再試行", "Retry"))
            self.update_button.configure(state="normal")
            messagebox.showerror("LiveCatch", str(exc))
            return
        self.destroy()

    def _reset_progress(self):
        self._progress = {
            "phase": "starting", "active": False, "streams": {}, "detail": "",
            "last_phase": "starting", "export_current": 0, "export_total": 0, "outputs": 0,
            "started_at": None, "elapsed": None,
        }
        self._render_progress()

    def _phase_text(self, phase: str) -> str:
        ja, en = PHASE_LABELS.get(phase, (phase, phase))
        return self._t(ja, en)

    def _handle_progress_event(self, event: dict):
        kind = event.get("event")
        if kind == "phase":
            phase = event.get("name", "starting")
            if phase == "exporting":
                phase = "postprocessing"
            self._progress["phase"] = phase
            self._progress["last_phase"] = phase
        elif kind == "progress":
            stream = event.get("stream", "media")
            state = self._progress["streams"].setdefault(stream, {})
            state.update(event)
            self._progress["detail"] = stream
        elif kind == "fragment":
            stream = event.get("stream", "media")
            state = self._progress["streams"].setdefault(stream, {})
            state.update(event)
            self._progress["detail"] = stream
        elif kind == "streams":
            self._progress["detail"] = self._t(
                f"音声・映像 {event.get('count', '?')}ストリームを処理中",
                f"Processing {event.get('count', '?')} audio/video streams")
        elif kind == "snapshot":
            self._progress["detail"] = self._t(
                f"現在まで取得: sequence {event.get('exclusive_sequence', '?')} まで",
                f"Snapshot cutoff: sequence {event.get('exclusive_sequence', '?')}")
        elif kind == "output":
            self._progress["outputs"] += 1
            self._progress["detail"] = self._t(
                f"保存済み {self._progress['outputs']}件: {event.get('path', '')}",
                f"Saved {self._progress['outputs']}: {event.get('path', '')}")
        elif kind == "done":
            started_at = self._progress.get("started_at")
            if isinstance(started_at, (int, float)):
                self._progress["elapsed"] = max(0.0, monotonic() - started_at)
            self._progress["active"] = False
            self._progress["phase"] = "done" if event.get("status") == "completed" else event.get("status", "failed")
            self._progress["detail"] = self._t(
                f"終了: {event.get('status', 'failed')}",
                f"Finished: {event.get('status', 'failed')}")
            if event.get("status") == "completed":
                self._progress["percent"] = 100.0
        self._render_progress()

    def _render_progress(self):
        if not hasattr(self, "phase_var"):
            return
        phase = self._progress.get("phase", "starting")
        self.phase_var.set(self._phase_text(phase))
        current_phase = phase if phase in PROGRESS_PHASES else self._progress.get("last_phase", "starting")
        current_index = PROGRESS_PHASES.index(current_phase)
        error_state = phase in {"failed", "cancelled", "forced"}
        for index, step_phase in enumerate(PROGRESS_PHASES):
            if error_state and step_phase == current_phase:
                state = "error"
            elif phase == "done" or index < current_index:
                state = "done"
            elif index == current_index:
                state = "active"
            else:
                state = "idle"
            background, foreground = PROGRESS_COLORS[state]
            step = self.phase_steps[step_phase]
            step.configure(text=("✓ " if state == "done" else "▶ " if state == "active" else "") + self._phase_text(step_phase),
                           bg=background, fg=foreground)
        detail = self._progress.get("detail", "")
        if not detail:
            known = []
            for state in self._progress.get("streams", {}).values():
                percent = state.get("percent")
                if isinstance(percent, (int, float)):
                    known.append(float(percent))
            if known:
                detail = self._t(f"全体の目安 {min(known):.1f}%", f"Overall estimate {min(known):.1f}%")
            elif self._progress.get("active"):
                detail = self._t("処理中…", "Working…")
        self.detail_var.set(detail)
        percent_values = []
        for state in self._progress.get("streams", {}).values():
            percent = state.get("percent")
            if isinstance(percent, (int, float)):
                percent_values.append(float(percent))
        if isinstance(self._progress.get("percent"), (int, float)):
            percent_values = [self._progress["percent"]]
        if percent_values:
            self.percent_var.set(f"{min(percent_values):.0f}%")
            self.progressbar.stop()
            self.progressbar.configure(mode="determinate", maximum=100, value=min(percent_values))
        elif self._progress.get("active"):
            self.percent_var.set("—")
            if self.progressbar.cget("mode") != "indeterminate":
                self.progressbar.configure(mode="indeterminate")
                self.progressbar.start(10)
        else:
            self.percent_var.set("0%")
            self.progressbar.stop()
            self.progressbar.configure(mode="determinate", maximum=100, value=0)
        self.metrics_var.set(self._progress_summary())

    @staticmethod
    def _format_bytes(value) -> str:
        if not isinstance(value, (int, float)) or value < 0:
            return "?"
        size = float(value)
        for unit in ("B", "KiB", "MiB", "GiB"):
            if size < 1024 or unit == "GiB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{size:.0f} B"
            size /= 1024
        return "?"

    def _progress_summary(self) -> str:
        parts = []
        elapsed = self._progress.get("elapsed")
        started_at = self._progress.get("started_at")
        if self._progress.get("active") and isinstance(started_at, (int, float)):
            elapsed = max(0.0, monotonic() - started_at)
        if isinstance(elapsed, (int, float)):
            parts.append(self._t(
                ("経過 " if self._progress.get("active") else "所要時間 ") + format_elapsed(elapsed),
                ("Elapsed " if self._progress.get("active") else "Duration ") + format_elapsed(elapsed),
            ))
        for stream, state in sorted(self._progress.get("streams", {}).items()):
            label = str(stream)
            percent = state.get("percent")
            if isinstance(percent, (int, float)):
                label += f" {float(percent):.1f}%"
            fragment_index, fragment_count = state.get("fragment_index"), state.get("fragment_count")
            if isinstance(fragment_index, int) and isinstance(fragment_count, int) and fragment_count > 0:
                label += f" ({fragment_index}/{fragment_count} fragments)"
            elif isinstance(state.get("downloaded_bytes"), int):
                downloaded = self._format_bytes(state["downloaded_bytes"])
                total = self._format_bytes(state.get("total_bytes"))
                label += f" ({downloaded}/{total})"
            if isinstance(state.get("speed"), (int, float)) and state["speed"] > 0:
                label += f" @ {self._format_bytes(state['speed'])}/s"
            parts.append(label)
        if self._progress.get("outputs"):
            parts.append(self._t(f"保存済み {self._progress['outputs']}件", f"Saved {self._progress['outputs']}"))
        return "  •  ".join(parts) or self._t("まだ進行情報はありません", "No progress data yet")

    def _poll(self):
        events = self.supervisor.events.drain()
        log_events = []
        for event in events:
            self._handle_progress_event(event)
            if event["event"] == "done":
                self.start_button.configure(state="normal")
                if self.closing:
                    self.destroy()
                    return
            if event["event"] not in {"phase", "progress", "fragment", "streams", "snapshot"}:
                log_events.append(event)
        if log_events:
            self._log("\n".join(e.get("message") or json.dumps(e, ensure_ascii=False) for e in log_events))
        if self._progress.get("active"):
            self._render_progress()
        self.after(100, self._poll)

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 2500:
            self.log.delete("1.0", f"{lines - 2000}.0")
        self.log.see("end")
        self.log.configure(state="disabled")


def main():
    LiveCatchApp().mainloop()
