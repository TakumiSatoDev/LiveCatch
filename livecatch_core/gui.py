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
from threading import Thread

from . import __version__
from .config import BROWSERS, QUALITY, ConfigStore, Settings
from .media import probe_cuda
from .options import ydl_options
from .supervisor import Supervisor
from .tools import find_tool

LABELS = {
    "mode": ("録画モード", "Recording mode"), "url": ("YouTube / Twitch URL", "YouTube / Twitch URL"),
    "save_dir": ("保存先", "Save folder"), "use_temp_dir": ("高速一時保存先を使う", "Use temp folder"),
    "temp_dir": ("一時保存先", "Temp folder"), "wait_seconds": ("予約確認間隔（秒）", "Reservation interval (seconds)"),
    "cookies_from_browser": ("ブラウザCookieを使う", "Use browser cookies"), "browser": ("ブラウザ", "Browser"),
    "live_from_start": ("予約録画でも先頭から取得", "Reservation: try from start"),
    "write_info_json": ("info.jsonを書き出す", "Write info.json"), "embed_metadata": ("メタデータを埋め込む", "Embed metadata"),
    "lightweight_catchup_postprocess": ("現在まで取得ではメタデータ省略", "Snapshot: skip metadata"),
    "quality_preset": ("画質", "Quality"), "output_format": ("保存形式", "Container"),
    "concurrent_fragments": ("同時fragment数（1〜32）", "Fragment concurrency (1–32)"),
    "output_template": ("出力テンプレート", "Output template"), "engine": ("取得エンジン", "Download engine"),
    "prefetch": ("先読み上限＝並列数×倍率", "Prefetch window multiplier"),
    "gpu_export": ("取得後に別ファイルへ変換", "Separate export after recording"),
    "gpu_device": ("GPU番号", "GPU device"), "export_height": ("出力の高さ（0＝元サイズ）", "Export height (0 = original)"),
    "gpu_jobs": ("同時変換数", "Concurrent exports"),
}
CHOICES = {"mode": ("reservation", "live_full", "catchup_stop"), "browser": BROWSERS,
           "quality_preset": tuple(QUALITY), "output_format": ("mp4", "mkv", "webm"),
           "engine": ("bounded", "stock"), "gpu_export": ("off", "auto", "cuda", "cpu"),
           "concurrent_fragments": (1, 2, 4, 8, 16, 32), "prefetch": (1, 2, 3, 4),
           "export_height": (0, 360, 480, 720, 1080, 1440, 2160), "gpu_jobs": (1, 2, 3, 4)}


class LiveCatchApp(tk.Tk):
    def __init__(self, store: ConfigStore | None = None):
        super().__init__()
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
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._poll)

    def _t(self, ja, en):
        return en if self.vars["language"].get() == "en" else ja

    def _build(self):
        self.body = ttk.Frame(self, padding=14)
        self.body.pack(fill="both", expand=True)
        header = ttk.Frame(self.body)
        header.pack(fill="x")
        ttk.Label(header, text=f"LiveCatch {__version__}", font=("", 20, "bold")).pack(side="left")
        lang = ttk.Combobox(header, textvariable=self.vars["language"], values=("ja", "en"), state="readonly", width=5)
        lang.pack(side="right")
        lang.bind("<<ComboboxSelected>>", self._rebuild)
        self.notebook = ttk.Notebook(self.body)
        self.notebook.pack(fill="x", pady=12)
        sections = [
            (("録画", "Recording"), ("mode", "url", "save_dir", "quality_preset", "output_format", "concurrent_fragments")),
            (("詳細", "Advanced"), ("use_temp_dir", "temp_dir", "output_template", "wait_seconds", "live_from_start",
                                  "write_info_json", "embed_metadata", "lightweight_catchup_postprocess",
                                  "cookies_from_browser", "browser", "engine", "prefetch")),
            (("GPU変換", "GPU export"), ("gpu_export", "gpu_device", "export_height", "gpu_jobs")),
        ]
        for title, names in sections:
            frame = ttk.Frame(self.notebook, padding=10)
            self.notebook.add(frame, text=self._t(*title))
            frame.columnconfigure(1, weight=1)
            for row, name in enumerate(names):
                label = self._t(*LABELS[name])
                var = self.vars[name]
                if isinstance(var, tk.BooleanVar):
                    ttk.Checkbutton(frame, text=label, variable=var).grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
                    continue
                ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
                if name in CHOICES:
                    widget = ttk.Combobox(frame, textvariable=var, values=CHOICES[name], state="readonly")
                else:
                    widget = ttk.Entry(frame, textvariable=var)
                widget.grid(row=row, column=1, sticky="ew", pady=4)
                if name in ("save_dir", "temp_dir"):
                    ttk.Button(frame, text="…", width=3, command=lambda n=name: self._browse(n)).grid(row=row, column=2)
        ttk.Label(self.body, wraplength=960, text=self._t(
            "reservation＝予約 / live_full＝終了まで / catchup_stop＝最初に観測した共通地点まで（YouTube DVR）。\n"
            "GPUは通信を速くしません。offは無変換保存、auto/cuda/cpuは元動画を残して別のMP4を作ります。",
            "reservation = wait / live_full = until end / catchup_stop = shared initial cutoff (YouTube DVR).\n"
            "GPU does not accelerate networking. off preserves source; auto/cuda/cpu create a separate lossy MP4."),
                  ).pack(anchor="w", pady=(0, 8))
        bar = ttk.Frame(self.body)
        bar.pack(fill="x")
        self.start_button = ttk.Button(bar, text=self._t("開始", "Start"), command=self._start)
        self.start_button.pack(side="left")
        ttk.Button(bar, text=self._t("停止", "Stop"), command=self.supervisor.stop).pack(side="left", padx=5)
        ttk.Button(bar, text=self._t("強制停止", "Force stop"), command=self._force).pack(side="left")
        ttk.Button(bar, text=self._t("設定・実行内容", "Execution settings"), command=self._preview).pack(side="left", padx=5)
        ttk.Button(bar, text=self._t("ツール / GPU確認", "Tools / GPU check"), command=self._diagnose).pack(side="left")
        ttk.Button(bar, text=self._t("保存先を開く", "Open folder"), command=self._open_folder).pack(side="left", padx=5)
        self.log = tk.Text(self.body, height=16, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True, pady=(12, 0))
        self.start_button.configure(state="disabled" if self.supervisor.active else "normal")

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
            self.store.save(settings)
            self.supervisor.start(settings)
            self.start_button.configure(state="disabled")
        except Exception as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _preview(self):
        try:
            settings = self.settings()
            options = ydl_options(settings, find_tool("ffmpeg"))
            options.pop("retry_sleep_functions", None)
            self._log(json.dumps({"engine": settings.engine, "yt_dlp": options,
                                  "gpu_export": settings.gpu_export}, ensure_ascii=False, indent=2))
        except Exception as exc:
            messagebox.showerror("LiveCatch", str(exc))

    def _diagnose(self):
        try:
            device = self.settings().gpu_device
        except ValueError as exc:
            messagebox.showerror("LiveCatch", str(exc))
            return
        def work():
            try:
                from yt_dlp.version import __version__ as version
            except ImportError:
                version = "missing: install requirements.txt"
            ffmpeg = find_tool("ffmpeg")
            gpu = probe_cuda(ffmpeg, device) if ffmpeg else (False, "ffmpeg missing")
            self.supervisor.events.put({"event": "log", "message": json.dumps(
                {"yt_dlp": version, "ffmpeg": ffmpeg, "ffprobe": find_tool("ffprobe"),
                 "deno": find_tool("deno"), "cuda_runtime": gpu}, ensure_ascii=False)})
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
            "処理中の結合・変換を中断します。未完成ファイルが残る場合があります。強制停止しますか？",
            "This interrupts muxing/encoding and may leave partial files. Force stop?")):
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

    def _poll(self):
        events = self.supervisor.events.drain()
        for event in events:
            if event["event"] == "done":
                self.start_button.configure(state="normal")
                if self.closing:
                    self.destroy()
                    return
        if events:
            self._log("\n".join(e.get("message") or json.dumps(e, ensure_ascii=False) for e in events))
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
