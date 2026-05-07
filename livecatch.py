import os
import sys
import re
import json
import time
import queue
import signal
import shutil
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "LiveCatch"
APP_VERSION = "1.1.1"
CONFIG_FILE = Path.home() / ".livecatch_config.json"

MODE_RESERVATION = "reservation"
MODE_LIVE_FULL = "live_full"
MODE_CATCHUP_STOP = "catchup_stop"

FRAG_RE = re.compile(r"^(?:(?P<stream>\d+):\s*)?.*?\(frag\s+(?P<current>\d+)\s*/\s*(?P<total>\d+)\)")

QUALITY_PRESETS = {
    "おすすめ：1080p以下": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "最高画質": "bv*+ba/b",
    "1440p以下": "bv*[height<=1440]+ba/b[height<=1440]/b",
    "1080p以下": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "720p以下": "bv*[height<=720]+ba/b[height<=720]/b",
    "480p以下": "bv*[height<=480]+ba/b[height<=480]/b",
    "360p以下": "bv*[height<=360]+ba/b[height<=360]/b",
}
DEFAULT_QUALITY_LABEL = "おすすめ：1080p以下"
DEFAULT_CONCURRENT_FRAGMENTS = 8
SPEED_PRESETS = ["1", "2", "4", "8", "16", "32", "64", "128"]
MAX_LOG_LINES = 2500
LOG_TRIM_LINES = 500
DOWNLOAD_LOG_INTERVAL_SEC = 0.25


def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_executable(name):
    candidates = []
    suffixes = [".exe", ""] if os.name == "nt" else [""]

    for suffix in suffixes:
        candidates.append(app_dir() / "tools" / f"{name}{suffix}")
        candidates.append(app_dir() / f"{name}{suffix}")

    for c in candidates:
        if c.exists():
            return str(c)

    found = shutil.which(name)
    if found:
        return found

    return None


def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(data):
    try:
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


class LiveCatchApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1010x765")
        self.minsize(930, 670)

        self.config_data = load_config()
        self.proc = None
        self.log_queue = queue.Queue()
        self.is_running = False

        self.run_started_at = 0.0
        self.catchup_frag_state = {}
        self.catchup_stop_sent = False
        self.catchup_candidate_since = None
        self.force_terminate_thread_started = False
        self.last_download_log_at = 0.0

        last_mode = self.config_data.get("mode", MODE_RESERVATION)
        if last_mode not in [MODE_RESERVATION, MODE_LIVE_FULL, MODE_CATCHUP_STOP]:
            last_mode = MODE_RESERVATION

        self.mode_var = tk.StringVar(value=last_mode)
        self.url_var = tk.StringVar(value=self.config_data.get("url", ""))
        self.save_dir_var = tk.StringVar(value=self.config_data.get("save_dir", str(Path.home() / "Videos" / "LiveCatch")))
        self.wait_var = tk.StringVar(value=str(self.config_data.get("wait_seconds", 30)))
        self.cookies_var = tk.BooleanVar(value=self.config_data.get("cookies_from_browser", False))
        self.browser_var = tk.StringVar(value=self.config_data.get("browser", "chrome"))
        self.from_start_var = tk.BooleanVar(value=self.config_data.get("live_from_start", True))
        self.info_json_var = tk.BooleanVar(value=self.config_data.get("write_info_json", True))
        self.metadata_var = tk.BooleanVar(value=self.config_data.get("embed_metadata", True))

        last_quality = self.config_data.get("quality_preset", DEFAULT_QUALITY_LABEL)
        if last_quality not in QUALITY_PRESETS:
            last_quality = DEFAULT_QUALITY_LABEL
        self.quality_preset_var = tk.StringVar(value=last_quality)
        self.concurrent_fragments_var = tk.StringVar(
            value=str(self.config_data.get("concurrent_fragments", DEFAULT_CONCURRENT_FRAGMENTS))
        )

        self.output_template_var = tk.StringVar(
            value=self.config_data.get(
                "output_template",
                "%(upload_date)s_%(channel)s_%(title)s/%(upload_date)s_%(title)s.%(ext)s",
            )
        )

        self._build_ui()
        self._apply_mode_ui()
        self.after(100, self._poll_log_queue)

    def _build_ui(self):
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(outer, text=f"LiveCatch v{APP_VERSION}", font=("", 20, "bold"))
        title.pack(anchor="w")

        self.subtitle = ttk.Label(
            outer,
            text="YouTubeライブの予約録画・配信中ライブの回収を行うGUIアプリです。",
            foreground="#555",
        )
        self.subtitle.pack(anchor="w", pady=(2, 14))

        mode_frame = ttk.LabelFrame(outer, text="録画モード", padding=10)
        mode_frame.pack(fill="x", pady=(0, 12))

        ttk.Radiobutton(
            mode_frame,
            text="1. 予約録画：配信待機枠URLを置いて、開始したら録画",
            variable=self.mode_var,
            value=MODE_RESERVATION,
            command=self._on_mode_changed,
        ).grid(row=0, column=0, sticky="w", pady=2)

        ttk.Radiobutton(
            mode_frame,
            text="2. 配信中ライブを最後まで録画：途中参加でも開始地点から追って、そのまま終了まで録画",
            variable=self.mode_var,
            value=MODE_LIVE_FULL,
            command=self._on_mode_changed,
        ).grid(row=1, column=0, sticky="w", pady=2)

        ttk.Radiobutton(
            mode_frame,
            text="3. 配信中ライブを現在まで取得：開始地点から現在地点まで取って、追いついたら停止",
            variable=self.mode_var,
            value=MODE_CATCHUP_STOP,
            command=self._on_mode_changed,
        ).grid(row=2, column=0, sticky="w", pady=2)

        self.mode_hint = ttk.Label(mode_frame, text="", foreground="#666")
        self.mode_hint.grid(row=3, column=0, sticky="w", pady=(8, 0))

        form = ttk.Frame(outer)
        form.pack(fill="x")

        self._row(form, 0, "ライブURL", self.url_var, width=92)

        save_row = ttk.Frame(form)
        save_row.grid(row=1, column=1, sticky="ew", pady=5)
        form.columnconfigure(1, weight=1)
        save_entry = ttk.Entry(save_row, textvariable=self.save_dir_var)
        save_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(save_row, text="保存先を選択", command=self._choose_save_dir).pack(side="left", padx=(8, 0))
        ttk.Label(form, text="保存先").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)

        self._row(form, 2, "出力テンプレート", self.output_template_var, width=92)

        options = ttk.LabelFrame(outer, text="録画オプション", padding=10)
        options.pack(fill="x", pady=(14, 10))

        self.wait_label = ttk.Label(options, text="開始確認間隔（秒）")
        self.wait_label.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)

        self.wait_entry = ttk.Entry(options, textvariable=self.wait_var, width=8)
        self.wait_entry.grid(row=0, column=1, sticky="w", pady=4)

        ttk.Label(options, text="速度（同時fragment数）").grid(row=0, column=2, sticky="e", padx=(28, 8), pady=4)
        speed_box = ttk.Combobox(options, textvariable=self.concurrent_fragments_var, values=SPEED_PRESETS, width=8, state="readonly")
        speed_box.grid(row=0, column=3, sticky="w", pady=4)
        speed_box.bind("<<ComboboxSelected>>", lambda _event: self.save_current_config())

        ttk.Label(options, text="画質").grid(row=0, column=4, sticky="e", padx=(28, 8), pady=4)
        quality_box = ttk.Combobox(options, textvariable=self.quality_preset_var, values=list(QUALITY_PRESETS.keys()), width=22, state="readonly")
        quality_box.grid(row=0, column=5, sticky="w", pady=4)
        quality_box.bind("<<ComboboxSelected>>", lambda _event: self.save_current_config())

        self.from_start_check = ttk.Checkbutton(
            options,
            text="DVR有効ならライブ先頭から取得を試す（--live-from-start）",
            variable=self.from_start_var,
        )
        self.from_start_check.grid(row=1, column=0, columnspan=4, sticky="w", pady=4)

        ttk.Checkbutton(options, text="info.jsonを書き出す", variable=self.info_json_var).grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Checkbutton(options, text="メタデータを埋め込む", variable=self.metadata_var).grid(row=3, column=0, columnspan=3, sticky="w", pady=4)

        ttk.Checkbutton(options, text="ブラウザCookieを使う", variable=self.cookies_var).grid(row=2, column=3, sticky="w", padx=(28, 8), pady=4)
        ttk.Label(options, text="ブラウザ").grid(row=2, column=4, sticky="e", padx=(8, 6), pady=4)
        browser_box = ttk.Combobox(options, textvariable=self.browser_var, values=["chrome", "edge", "firefox", "brave", "vivaldi", "opera"], width=12, state="readonly")
        browser_box.grid(row=2, column=5, sticky="w", pady=4)

        speed_hint = ttk.Label(
            options,
            text="速度はダウンロード並列数です。DVRに溜まった過去部分だけ速く回収できます。現在以降の未来部分は実時間以上には進みません。",
            foreground="#666",
        )
        speed_hint.grid(row=4, column=0, columnspan=6, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(6, 10))

        self.start_btn = ttk.Button(buttons, text="開始", command=self.start_recording)
        self.start_btn.pack(side="left")

        self.stop_btn = ttk.Button(buttons, text="停止", command=self.stop_recording, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        ttk.Button(buttons, text="コマンド確認", command=self.show_command).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="ツール確認", command=self.check_tools).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="保存先を開く", command=self.open_save_dir).pack(side="left", padx=(8, 0))

        status = ttk.LabelFrame(outer, text="ログ", padding=8)
        status.pack(fill="both", expand=True)

        self.log_text = tk.Text(status, height=18, wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(status, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)

        help_text = (
            "必要: yt-dlp と ffmpeg。PATHに入っていなくても、アプリ横の tools フォルダに置けば使えます。"
            " build_exe.bat 実行時に tools が無ければ自動導入します。"
        )
        ttk.Label(outer, text=help_text, foreground="#666").pack(anchor="w", pady=(8, 0))

    def _row(self, parent, row, label, var, width=60):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=5)

    def _on_mode_changed(self):
        self.save_current_config()
        self._apply_mode_ui()

    def _apply_mode_ui(self):
        mode = self.mode_var.get()

        if mode == MODE_RESERVATION:
            self.subtitle.configure(text="予約録画モード：配信待機枠URLを置いておくと、開始まで待機して自動録画します。")
            self.mode_hint.configure(
                text="事前にURLが分かっている配信用。未開始なら待機し、配信開始後は終了まで録画します。"
            )
            self.start_btn.configure(text="予約録画を開始")
            self.wait_label.configure(text="開始確認間隔（秒）")
            self.wait_entry.configure(state="normal")
            self.from_start_check.configure(state="normal")

        elif mode == MODE_LIVE_FULL:
            self.subtitle.configure(text="配信中ライブを最後まで録画：途中参加でも開始地点から追って、そのまま配信終了まで録画します。")
            self.mode_hint.configure(
                text="途中で気づいたけど、最初から最後まで素材として残したい時用。DVR有効なら開始地点から追いかけます。"
            )
            self.start_btn.configure(text="開始地点から最後まで録画")
            self.wait_label.configure(text="確認間隔（このモードでは未使用）")
            self.wait_entry.configure(state="disabled")
            self.from_start_var.set(True)
            self.from_start_check.configure(state="disabled")

        elif mode == MODE_CATCHUP_STOP:
            self.subtitle.configure(text="配信中ライブを現在まで取得：開始地点から現在地点まで取って、追いついたら停止します。")
            self.mode_hint.configure(
                text="切り抜き用に『開始〜今』だけ欲しい時用。追いつき判定後、録画プロセスへ停止要求を送ります。"
            )
            self.start_btn.configure(text="現在地点まで取得")
            self.wait_label.configure(text="確認間隔（このモードでは未使用）")
            self.wait_entry.configure(state="disabled")
            self.from_start_var.set(True)
            self.from_start_check.configure(state="disabled")

    def _choose_save_dir(self):
        d = filedialog.askdirectory(initialdir=self.save_dir_var.get() or str(Path.home()))
        if d:
            self.save_dir_var.set(d)
            self.save_current_config()

    def check_tools(self):
        yt = find_executable("yt-dlp")
        ff = find_executable("ffmpeg")

        self._log("\nツール確認:\n")
        self._log(f"アプリフォルダ: {app_dir()}\n")
        self._log(f"yt-dlp: {yt or '見つかりません'}\n")
        self._log(f"ffmpeg: {ff or '見つかりません'}\n\n")

        if yt:
            self._run_version_check([yt, "--version"], "yt-dlp")
        if ff:
            self._run_version_check([ff, "-version"], "ffmpeg")

    def _run_version_check(self, cmd, label):
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=10)
            first = out.splitlines()[0] if out.splitlines() else out
            self._log(f"{label} version: {first}\n")
        except Exception as e:
            self._log(f"{label} version check error: {e}\n")

    def _get_concurrent_fragments(self):
        try:
            value = int(self.concurrent_fragments_var.get())
        except ValueError:
            value = DEFAULT_CONCURRENT_FRAGMENTS

        if value < 1:
            value = 1
        if value > 128:
            value = 128
        return value

    def _get_format_selector(self):
        label = self.quality_preset_var.get()
        return QUALITY_PRESETS.get(label, QUALITY_PRESETS[DEFAULT_QUALITY_LABEL])

    def _validate(self):
        if not self.url_var.get().strip():
            messagebox.showerror(APP_NAME, "YouTubeライブURLを入力してください。")
            return False

        if self.mode_var.get() == MODE_RESERVATION:
            try:
                wait = int(self.wait_var.get())
                if wait < 5:
                    raise ValueError
            except ValueError:
                messagebox.showerror(APP_NAME, "開始確認間隔は5秒以上の整数にしてください。")
                return False

        if self._get_concurrent_fragments() >= 64:
            proceed = messagebox.askyesno(
                APP_NAME,
                "同時fragment数がかなり高い設定です。\n\n"
                "回線やYouTube側の状況によっては失敗・速度低下・一時制限の原因になる場合があります。\n"
                "このまま続行しますか？"
            )
            if not proceed:
                return False

        if find_executable("yt-dlp") is None:
            messagebox.showerror(
                APP_NAME,
                "yt-dlp が見つかりません。\n\n"
                "同梱の install_tools.ps1 を実行してください。\n"
                "または tools フォルダに yt-dlp.exe を置いてください。"
            )
            return False

        if find_executable("ffmpeg") is None:
            messagebox.showwarning(
                APP_NAME,
                "ffmpeg が見つかりません。\n\n"
                "mp4結合やメタデータ埋め込みに失敗する可能性があります。\n"
                "同梱の install_tools.ps1 を実行するのがおすすめです。"
            )

        return True

    def build_command(self):
        mode = self.mode_var.get()
        save_dir = Path(self.save_dir_var.get()).expanduser()
        out_tmpl = self.output_template_var.get().strip() or "%(upload_date)s_%(title)s.%(ext)s"
        output_path = str(save_dir / out_tmpl)

        yt_dlp_path = find_executable("yt-dlp") or "yt-dlp"
        ffmpeg_path = find_executable("ffmpeg")
        ffmpeg_dir = str(Path(ffmpeg_path).parent) if ffmpeg_path else None

        concurrent_fragments = self._get_concurrent_fragments()
        format_selector = self._get_format_selector()

        cmd = [
            yt_dlp_path,
            "-N", str(concurrent_fragments),
            "-f", format_selector,
            "--merge-output-format", "mp4",
            "--newline",
            "-o", output_path,
        ]

        if ffmpeg_dir:
            cmd.extend(["--ffmpeg-location", ffmpeg_dir])

        if mode == MODE_RESERVATION:
            cmd.extend(["--wait-for-video", str(int(self.wait_var.get()))])
            if self.from_start_var.get():
                cmd.append("--live-from-start")
        elif mode == MODE_LIVE_FULL:
            cmd.append("--live-from-start")
        elif mode == MODE_CATCHUP_STOP:
            cmd.append("--live-from-start")
        else:
            raise ValueError("不明なモードです。")

        if self.info_json_var.get():
            cmd.append("--write-info-json")
        if self.metadata_var.get():
            cmd.append("--embed-metadata")
        if self.cookies_var.get():
            cmd.extend(["--cookies-from-browser", self.browser_var.get()])

        cmd.append(self.url_var.get().strip())
        return cmd

    def save_current_config(self):
        try:
            wait_seconds = int(self.wait_var.get() or 30)
        except ValueError:
            wait_seconds = 30

        save_config({
            "version": APP_VERSION,
            "mode": self.mode_var.get(),
            "url": self.url_var.get(),
            "save_dir": self.save_dir_var.get(),
            "wait_seconds": wait_seconds,
            "cookies_from_browser": self.cookies_var.get(),
            "browser": self.browser_var.get(),
            "live_from_start": self.from_start_var.get(),
            "write_info_json": self.info_json_var.get(),
            "embed_metadata": self.metadata_var.get(),
            "quality_preset": self.quality_preset_var.get(),
            "concurrent_fragments": self._get_concurrent_fragments(),
            "output_template": self.output_template_var.get(),
        })

    def show_command(self):
        try:
            cmd = self.build_command()
            self._log("実行予定コマンド:\n" + subprocess.list2cmdline(cmd) + "\n\n")
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))

    def start_recording(self):
        if self.is_running:
            return
        if not self._validate():
            return

        self.save_current_config()
        Path(self.save_dir_var.get()).expanduser().mkdir(parents=True, exist_ok=True)

        self.run_started_at = time.time()
        self.catchup_frag_state = {}
        self.catchup_stop_sent = False
        self.catchup_candidate_since = None
        self.force_terminate_thread_started = False
        self.last_download_log_at = 0.0

        cmd = self.build_command()
        mode = self.mode_var.get()
        mode_label = {
            MODE_RESERVATION: "予約録画",
            MODE_LIVE_FULL: "配信中ライブを最後まで録画",
            MODE_CATCHUP_STOP: "配信中ライブを現在まで取得",
        }.get(mode, "録画")

        self._log(f"{mode_label}を開始しました。\n")
        self._log(f"画質: {self.quality_preset_var.get()} / 速度: 同時fragment数 {self._get_concurrent_fragments()}\n")
        if mode == MODE_CATCHUP_STOP:
            self._log("追いつき判定: fragment の現在値が最新値付近に到達したら停止要求を送ります。\n")
        self._log(subprocess.list2cmdline(cmd) + "\n\n")

        self.is_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        t = threading.Thread(target=self._run_process, args=(cmd,), daemon=True)
        t.start()

    def _run_process(self, cmd):
        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )

            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self._maybe_stop_when_caught_up(line)
                if self._should_display_log(line):
                    self.log_queue.put(line)

            code = self.proc.wait()
            self.log_queue.put(f"\n録画プロセスが終了しました。終了コード: {code}\n")
        except Exception as e:
            self.log_queue.put(f"\nエラー: {e}\n")
        finally:
            self.proc = None
            self.log_queue.put("__PROCESS_DONE__")

    def _should_display_log(self, line):
        # yt-dlp can emit a huge number of fragment progress lines.
        # The catch-up detector still reads every line, but the GUI only displays
        # a throttled subset so long recordings do not make Tkinter sluggish.
        if "[download]" in line and "frag" in line:
            now = time.time()
            if now - self.last_download_log_at < DOWNLOAD_LOG_INTERVAL_SEC:
                return False
            self.last_download_log_at = now
        return True

    def _maybe_stop_when_caught_up(self, line):
        if self.mode_var.get() != MODE_CATCHUP_STOP:
            return
        if self.catchup_stop_sent:
            return
        if not self.proc or self.proc.poll() is not None:
            return

        match = FRAG_RE.search(line)
        if not match:
            return

        stream_id = match.group("stream") or "default"
        current = int(match.group("current"))
        total = int(match.group("total"))
        now = time.time()

        self.catchup_frag_state[stream_id] = {
            "current": current,
            "total": total,
            "updated_at": now,
        }

        if now - self.run_started_at < 15:
            return
        if total < 20:
            return

        active_states = [
            s for s in self.catchup_frag_state.values()
            if now - s["updated_at"] <= 10
        ]
        if not active_states:
            return

        margin = 1
        all_caught = all(s["current"] >= s["total"] - margin for s in active_states)

        if all_caught:
            if self.catchup_candidate_since is None:
                self.catchup_candidate_since = now
                return

            if now - self.catchup_candidate_since >= 2:
                self.catchup_stop_sent = True
                self.log_queue.put(
                    "\n現在地点に追いついたと判断しました。録画プロセスへ停止要求を送ります。\n"
                    "※ yt-dlp側の後処理として、結合やメタデータ埋め込みに少し時間がかかる場合があります。\n"
                )
                self._send_graceful_interrupt()
        else:
            self.catchup_candidate_since = None

    def _send_graceful_interrupt(self):
        if not self.proc or self.proc.poll() is not None:
            return

        try:
            if os.name == "nt":
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.proc.send_signal(signal.SIGINT)
        except Exception as e:
            self.log_queue.put(f"通常停止に失敗したため terminate を試します: {e}\n")
            try:
                self.proc.terminate()
            except Exception as e2:
                self.log_queue.put(f"terminate にも失敗しました: {e2}\n")

        if not self.force_terminate_thread_started:
            self.force_terminate_thread_started = True
            threading.Thread(target=self._force_terminate_later, daemon=True).start()

    def _force_terminate_later(self):
        time.sleep(45)
        if self.proc and self.proc.poll() is None:
            self.log_queue.put("\n停止要求から45秒経過したため、プロセスを強制終了します。\n")
            try:
                self.proc.terminate()
            except Exception as e:
                self.log_queue.put(f"強制終了に失敗しました: {e}\n")

    def stop_recording(self):
        if self.proc and self.proc.poll() is None:
            self._log("\n停止要求を送信しました。ファイルの後処理に少し時間がかかる場合があります。\n")
            self._send_graceful_interrupt()

    def open_save_dir(self):
        d = Path(self.save_dir_var.get()).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(d))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(d)])
        else:
            subprocess.Popen(["xdg-open", str(d)])

    def _poll_log_queue(self):
        processed = 0
        try:
            while processed < 200:
                msg = self.log_queue.get_nowait()
                processed += 1
                if msg == "__PROCESS_DONE__":
                    self.is_running = False
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self._apply_mode_ui()
                else:
                    self._log(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _log(self, msg):
        self.log_text.insert("end", msg)
        self._trim_log_if_needed()
        self.log_text.see("end")

    def _trim_log_if_needed(self):
        try:
            line_count = int(self.log_text.index("end-1c").split(".")[0])
            if line_count > MAX_LOG_LINES:
                self.log_text.delete("1.0", f"{LOG_TRIM_LINES}.0")
        except Exception:
            pass


if __name__ == "__main__":
    app = LiveCatchApp()
    app.mainloop()
