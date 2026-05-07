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
from urllib.parse import urlparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "LiveCatch"
APP_VERSION = "2.1.3"
CONFIG_FILE = Path.home() / ".livecatch_config.json"

MODE_RESERVATION = "reservation"
MODE_LIVE_FULL = "live_full"
MODE_CATCHUP_STOP = "catchup_stop"

LANG_JA = "ja"
LANG_EN = "en"
SUPPORTED_LANGUAGES = [LANG_JA, LANG_EN]

PLATFORM_YOUTUBE = "youtube"
PLATFORM_TWITCH = "twitch"
PLATFORM_UNKNOWN = "unknown"

PLATFORM_LABELS = {
    LANG_JA: {
        PLATFORM_YOUTUBE: "YouTube",
        PLATFORM_TWITCH: "Twitch",
        PLATFORM_UNKNOWN: "不明",
    },
    LANG_EN: {
        PLATFORM_YOUTUBE: "YouTube",
        PLATFORM_TWITCH: "Twitch",
        PLATFORM_UNKNOWN: "Unknown",
    },
}

DEFAULT_OUTPUT_TEMPLATE = "%(extractor_key)s/%(upload_date)s_%(channel)s_%(title)s/%(upload_date)s_%(title)s.%(ext)s"
LEGACY_DEFAULT_OUTPUT_TEMPLATE = "%(upload_date)s_%(channel)s_%(title)s/%(upload_date)s_%(title)s.%(ext)s"

FRAG_RE = re.compile(r"^(?:(?P<stream>\d+):\s*)?.*?\(frag\s+(?P<current>\d+)\s*/\s*(?P<total>\d+)\)")

QUALITY_PRESET_DEFS = [
    (
        "recommended_1080p",
        "bv*[height<=1080]+ba/b[height<=1080]/b",
        {
            LANG_JA: "おすすめ：1080p以下",
            LANG_EN: "Recommended: 1080p or lower",
        },
    ),
    (
        "catchup_720p30",
        "bv*[height<=720][fps<=30]+ba/b[height<=720][fps<=30]/bv*[height<=720]+ba/b[height<=720]/b",
        {
            LANG_JA: "追いつき優先：720p30以下",
            LANG_EN: "Catch-up priority: 720p30 or lower",
        },
    ),
    (
        "catchup_480p30",
        "bv*[height<=480][fps<=30]+ba/b[height<=480][fps<=30]/bv*[height<=480]+ba/b[height<=480]/b",
        {
            LANG_JA: "追いつき優先：480p30以下",
            LANG_EN: "Catch-up priority: 480p30 or lower",
        },
    ),
    ("best", "bv*+ba/b", {LANG_JA: "最高画質", LANG_EN: "Best quality"}),
    ("1440p", "bv*[height<=1440]+ba/b[height<=1440]/b", {LANG_JA: "1440p以下", LANG_EN: "1440p or lower"}),
    ("1080p", "bv*[height<=1080]+ba/b[height<=1080]/b", {LANG_JA: "1080p以下", LANG_EN: "1080p or lower"}),
    ("720p", "bv*[height<=720]+ba/b[height<=720]/b", {LANG_JA: "720p以下", LANG_EN: "720p or lower"}),
    ("480p", "bv*[height<=480]+ba/b[height<=480]/b", {LANG_JA: "480p以下", LANG_EN: "480p or lower"}),
    ("360p", "bv*[height<=360]+ba/b[height<=360]/b", {LANG_JA: "360p以下", LANG_EN: "360p or lower"}),
]
QUALITY_PRESETS = {key: selector for key, selector, _labels in QUALITY_PRESET_DEFS}
QUALITY_PRESET_LABELS = {key: labels for key, _selector, labels in QUALITY_PRESET_DEFS}
QUALITY_LABEL_TO_KEY = {
    label: key
    for key, _selector, labels in QUALITY_PRESET_DEFS
    for label in labels.values()
}
DEFAULT_QUALITY_KEY = "recommended_1080p"
DEFAULT_CONCURRENT_FRAGMENTS = 8
SPEED_PRESETS = ["1", "2", "4", "8", "16", "32", "64", "128"]
OUTPUT_FORMATS = ["mp4", "mkv", "webm"]
DEFAULT_OUTPUT_FORMAT = "mp4"
MAX_LOG_LINES = 2500
LOG_TRIM_LINES = 500
DOWNLOAD_LOG_INTERVAL_SEC = 0.25
CATCHUP_PROGRESS_LOG_INTERVAL_SEC = 10.0

TRANSLATIONS = {
    LANG_JA: {
        "menu_language": "言語",
        "menu_lang_ja": "日本語",
        "menu_lang_en": "English",
        "subtitle_default": "YouTube/Twitchライブの予約録画・配信中ライブの回収を行うGUIアプリです。",
        "mode_frame": "録画モード",
        "mode_reservation_radio": "1. 予約録画：配信待機枠URLを置いて、開始したら録画",
        "mode_live_full_radio": "2. 配信中ライブを最後まで録画：途中参加でも開始地点から追って、そのまま終了まで録画",
        "mode_catchup_stop_radio": "3. 配信中ライブを現在まで取得：開始地点から現在地点まで取って、追いついたら停止",
        "url_label": "ライブURL",
        "save_dir_label": "保存先",
        "choose_save_dir": "保存先を選択",
        "temp_dir_label": "一時保存先",
        "use_temp_dir": "高速一時保存先を使う",
        "choose_temp_dir": "一時保存先を選択",
        "output_template_label": "出力テンプレート",
        "options_frame": "録画オプション",
        "wait_label_reservation": "開始確認間隔（秒）",
        "wait_label_unused": "確認間隔（このモードでは未使用）",
        "speed_label": "速度（同時fragment数）",
        "quality_label": "画質",
        "output_format_label": "保存形式",
        "from_start": "対応サイトでライブ先頭から取得を試す（--live-from-start）",
        "write_info_json": "info.jsonを書き出す",
        "embed_metadata": "メタデータを埋め込む",
        "lightweight_catchup": "現在まで取得ではメタデータ埋め込みを省略して軽くする",
        "use_browser_cookies": "ブラウザCookieを使う",
        "browser_label": "ブラウザ",
        "speed_hint": "速度はダウンロード並列数です。DVRやライブ履歴に溜まった過去部分だけ速く回収できます。現在以降の未来部分は実時間以上には進みません。",
        "start_default": "開始",
        "start_reservation": "予約録画を開始",
        "start_live_full": "開始地点から最後まで録画",
        "start_catchup_stop": "現在地点まで取得",
        "stop": "停止",
        "show_command": "コマンド確認",
        "check_tools": "ツール確認",
        "open_save_dir": "保存先を開く",
        "log_frame": "ログ",
        "help_text": "必要: yt-dlp と ffmpeg。YouTube/Twitch URLを自動判定します。 PATHに入っていなくても、アプリ横の tools フォルダに置けば使えます。",
        "subtitle_reservation": "予約録画モード：配信待機枠URLを置いておくと、開始まで待機して自動録画します。",
        "hint_reservation": "事前にURLが分かっている配信用。YouTube/Twitch URLを判定し、未開始なら待機して録画します。",
        "subtitle_live_full": "配信中ライブを最後まで録画：途中参加でも開始地点から追って、そのまま配信終了まで録画します。",
        "hint_live_full": "途中で気づいたけど、最初から最後まで素材として残したい時用。対応サイトでは開始地点から追いかけます。",
        "subtitle_catchup_stop": "配信中ライブを現在まで取得：開始地点から現在地点まで取って、追いついたら停止します。",
        "hint_catchup_stop": "切り抜き用に『開始〜今』だけ欲しい時用。fragmentログで追いつき判定できたら停止要求を送ります。",
        "err_url_required": "YouTube または Twitch のライブURLを入力してください。",
        "err_url_unsupported": "対応しているURLは YouTube または Twitch です。",
        "err_temp_dir_required": "高速一時保存先を使う場合は、一時保存先フォルダを指定してください。",
        "err_wait_seconds": "開始確認間隔は5秒以上の整数にしてください。",
        "high_speed_warning": "同時fragment数がかなり高い設定です。\n\n回線や配信サイト側の状況によっては失敗・速度低下・一時制限の原因になる場合があります。\nこのまま続行しますか？",
        "err_ytdlp_missing": "yt-dlp が見つかりません。\n\n同梱の install_tools.ps1 を実行してください。\nまたは tools フォルダに yt-dlp.exe を置いてください。",
        "warn_ffmpeg_missing": "ffmpeg が見つかりません。\n\nmp4結合やメタデータ埋め込みに失敗する可能性があります。\n同梱の install_tools.ps1 を実行するのがおすすめです。",
        "err_unknown_mode": "不明なモードです。",
        "tools_check_header": "\nツール確認:\n",
        "app_folder": "アプリフォルダ",
        "not_found": "見つかりません",
        "version_check_error": "{label} version check error: {error}\n",
        "detected_platform": "判定プラットフォーム: {platform}\n",
        "command_preview": "実行予定コマンド:\n{command}\n\n",
        "mode_label_reservation": "予約録画",
        "mode_label_live_full": "配信中ライブを最後まで録画",
        "mode_label_catchup_stop": "配信中ライブを現在まで取得",
        "mode_label_default": "録画",
        "recording_started": "{mode_label}を開始しました。\n",
        "platform_log": "プラットフォーム: {platform}\n",
        "settings_log": "画質: {quality} / 形式: {output_format} / 速度: 同時fragment数 {speed}\n",
        "temp_dir_log": "一時保存先: {temp_dir}\n",
        "lightweight_log": "軽量化: 現在まで取得ではメタデータ埋め込みを省略します。\n",
        "catchup_notice": "追いつき判定: fragment の現在値が最新値付近に到達したら停止要求を送ります。\n",
        "twitch_catchup_notice": "Twitchの追いつき停止は yt-dlp のfragmentログが取得できる場合に動作します。\n",
        "process_finished": "\n録画プロセスが終了しました。終了コード: {code}\n",
        "process_error": "\nエラー: {error}\n",
        "caught_up_stop": "\n現在地点に追いついたと判断しました。録画プロセスへ停止要求を送ります。\n※ yt-dlp側の後処理として、結合やメタデータ埋め込みに少し時間がかかる場合があります。\n",
        "catchup_progress": "追いつき状況: {parts}\n",
        "catchup_part": "{stream_id}: {current}/{total} 残{remaining} / {rate:.1f} frag/s{eta}",
        "eta_remaining": " / 現在地点まで約{duration}",
        "eta_near_live": " / 現在地点付近",
        "hours_minutes": "{hours}時間{minutes}分",
        "minutes_seconds": "{minutes}分{seconds}秒",
        "seconds": "{seconds}秒",
        "interrupt_failed": "通常停止に失敗したため terminate を試します: {error}\n",
        "terminate_failed": "terminate にも失敗しました: {error}\n",
        "force_terminate": "\n停止要求から45秒経過したため、プロセスを強制終了します。\n",
        "force_terminate_failed": "強制終了に失敗しました: {error}\n",
        "manual_stop": "\n停止要求を送信しました。ファイルの後処理に少し時間がかかる場合があります。\n",
    },
    LANG_EN: {
        "menu_language": "Language",
        "menu_lang_ja": "Japanese",
        "menu_lang_en": "English",
        "subtitle_default": "A GUI app for reservation recording and catching up YouTube/Twitch livestreams.",
        "mode_frame": "Recording Mode",
        "mode_reservation_radio": "1. Reservation recording: wait for a scheduled stream URL, then record when it starts",
        "mode_live_full_radio": "2. Record active livestream to the end: catch up from the beginning, then continue until the stream ends",
        "mode_catchup_stop_radio": "3. Download active livestream up to now: catch up from the beginning, then stop",
        "url_label": "Livestream URL",
        "save_dir_label": "Save Folder",
        "choose_save_dir": "Choose Folder",
        "temp_dir_label": "Temp Folder",
        "use_temp_dir": "Use fast temp folder",
        "choose_temp_dir": "Choose Temp Folder",
        "output_template_label": "Output Template",
        "options_frame": "Recording Options",
        "wait_label_reservation": "Start Check Interval (sec)",
        "wait_label_unused": "Check Interval (unused in this mode)",
        "speed_label": "Speed (concurrent fragments)",
        "quality_label": "Quality",
        "output_format_label": "Output Format",
        "from_start": "Try to download from livestream start on supported sites (--live-from-start)",
        "write_info_json": "Write info.json",
        "embed_metadata": "Embed metadata",
        "lightweight_catchup": "Skip metadata embedding in download-up-to-now mode for lighter post-processing",
        "use_browser_cookies": "Use browser cookies",
        "browser_label": "Browser",
        "speed_hint": "Speed controls download concurrency. It only speeds up past content already buffered in DVR/live history. Future live content cannot be downloaded faster than real time.",
        "start_default": "Start",
        "start_reservation": "Start Reservation Recording",
        "start_live_full": "Record From Start To End",
        "start_catchup_stop": "Download Up To Now",
        "stop": "Stop",
        "show_command": "Show Command",
        "check_tools": "Check Tools",
        "open_save_dir": "Open Save Folder",
        "log_frame": "Log",
        "help_text": "Required: yt-dlp and ffmpeg. YouTube/Twitch URLs are detected automatically. If they are not on PATH, place them in the tools folder next to the app.",
        "subtitle_reservation": "Reservation mode: keep a scheduled stream URL ready and record automatically when it starts.",
        "hint_reservation": "For streams whose URL is known in advance. LiveCatch detects YouTube/Twitch URLs and waits until the stream starts.",
        "subtitle_live_full": "Record active livestream to the end: catch up from the beginning, then keep recording until the stream ends.",
        "hint_live_full": "Use this when you joined late but still want material from the beginning through the end. Supported sites can be caught up from the start.",
        "subtitle_catchup_stop": "Download active livestream up to now: start from the beginning and stop after catching up.",
        "hint_catchup_stop": "For clipping workflows that only need start-to-now material. LiveCatch stops when fragment logs indicate it caught up.",
        "err_url_required": "Enter a YouTube or Twitch livestream URL.",
        "err_url_unsupported": "Supported URLs are YouTube and Twitch URLs.",
        "err_temp_dir_required": "Specify a temp folder when fast temp folder is enabled.",
        "err_wait_seconds": "The start check interval must be an integer of at least 5 seconds.",
        "high_speed_warning": "The concurrent fragment count is very high.\n\nDepending on your connection and the streaming site, this may cause failures, slowdowns, or temporary limits.\nContinue anyway?",
        "err_ytdlp_missing": "yt-dlp was not found.\n\nRun the included install_tools.ps1.\nOr place yt-dlp.exe in the tools folder.",
        "warn_ffmpeg_missing": "ffmpeg was not found.\n\nMP4 merging or metadata embedding may fail.\nRunning the included install_tools.ps1 is recommended.",
        "err_unknown_mode": "Unknown recording mode.",
        "tools_check_header": "\nTool check:\n",
        "app_folder": "App folder",
        "not_found": "not found",
        "version_check_error": "{label} version check error: {error}\n",
        "detected_platform": "Detected platform: {platform}\n",
        "command_preview": "Command to run:\n{command}\n\n",
        "mode_label_reservation": "Reservation recording",
        "mode_label_live_full": "Record active livestream to the end",
        "mode_label_catchup_stop": "Download active livestream up to now",
        "mode_label_default": "Recording",
        "recording_started": "Started {mode_label}.\n",
        "platform_log": "Platform: {platform}\n",
        "settings_log": "Quality: {quality} / Format: {output_format} / Speed: {speed} concurrent fragments\n",
        "temp_dir_log": "Temp folder: {temp_dir}\n",
        "lightweight_log": "Lightweight mode: metadata embedding is skipped for download-up-to-now mode.\n",
        "catchup_notice": "Catch-up detection: LiveCatch will request stop when the current fragment reaches the latest known fragment.\n",
        "twitch_catchup_notice": "Twitch catch-up stop works when yt-dlp exposes fragment logs with current and total values.\n",
        "process_finished": "\nRecording process finished. Exit code: {code}\n",
        "process_error": "\nError: {error}\n",
        "caught_up_stop": "\nLiveCatch judged that the download caught up to the current live point. Sending a stop request to the recording process.\nPost-processing such as merging and metadata embedding may take some time.\n",
        "catchup_progress": "Catch-up status: {parts}\n",
        "catchup_part": "{stream_id}: {current}/{total} remaining {remaining} / {rate:.1f} frag/s{eta}",
        "eta_remaining": " / about {duration} to current live point",
        "eta_near_live": " / near current live point",
        "hours_minutes": "{hours}h {minutes}m",
        "minutes_seconds": "{minutes}m {seconds}s",
        "seconds": "{seconds}s",
        "interrupt_failed": "Graceful stop failed. Trying terminate: {error}\n",
        "terminate_failed": "terminate also failed: {error}\n",
        "force_terminate": "\n45 seconds passed after the stop request. Force terminating the process.\n",
        "force_terminate_failed": "Force terminate failed: {error}\n",
        "manual_stop": "\nStop request sent. File post-processing may take some time.\n",
    },
}


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


def detect_platform(url):
    raw_url = (url or "").strip()
    if not raw_url:
        return PLATFORM_UNKNOWN

    parse_target = raw_url if "://" in raw_url else f"https://{raw_url}"
    try:
        parsed = urlparse(parse_target)
    except Exception:
        return PLATFORM_UNKNOWN

    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]

    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        return PLATFORM_YOUTUBE
    if host == "youtube-nocookie.com" or host.endswith(".youtube-nocookie.com"):
        return PLATFORM_YOUTUBE
    if host == "twitch.tv" or host.endswith(".twitch.tv"):
        return PLATFORM_TWITCH

    return PLATFORM_UNKNOWN


def platform_label(platform, language=LANG_JA):
    labels = PLATFORM_LABELS.get(language, PLATFORM_LABELS[LANG_JA])
    return labels.get(platform, labels[PLATFORM_UNKNOWN])


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
        self.last_catchup_progress_log_at = 0.0
        self.catchup_progress_baseline = {}

        last_mode = self.config_data.get("mode", MODE_RESERVATION)
        if last_mode not in [MODE_RESERVATION, MODE_LIVE_FULL, MODE_CATCHUP_STOP]:
            last_mode = MODE_RESERVATION

        last_language = self.config_data.get("language", LANG_JA)
        if last_language not in SUPPORTED_LANGUAGES:
            last_language = LANG_JA

        self.language_var = tk.StringVar(value=last_language)
        self.current_language = last_language
        self.mode_var = tk.StringVar(value=last_mode)
        self.url_var = tk.StringVar(value=self.config_data.get("url", ""))
        self.save_dir_var = tk.StringVar(value=self.config_data.get("save_dir", str(Path.home() / "Videos" / "LiveCatch")))
        self.use_temp_dir_var = tk.BooleanVar(value=self.config_data.get("use_temp_dir", False))
        self.temp_dir_var = tk.StringVar(value=self.config_data.get("temp_dir", str(Path.home() / "Videos" / "LiveCatch_temp")))
        self.wait_var = tk.StringVar(value=str(self.config_data.get("wait_seconds", 30)))
        self.cookies_var = tk.BooleanVar(value=self.config_data.get("cookies_from_browser", False))
        self.browser_var = tk.StringVar(value=self.config_data.get("browser", "chrome"))
        self.from_start_var = tk.BooleanVar(value=self.config_data.get("live_from_start", True))
        self.info_json_var = tk.BooleanVar(value=self.config_data.get("write_info_json", True))
        self.metadata_var = tk.BooleanVar(value=self.config_data.get("embed_metadata", True))
        self.lightweight_catchup_var = tk.BooleanVar(value=self.config_data.get("lightweight_catchup_postprocess", False))

        last_output_format = self.config_data.get("output_format", DEFAULT_OUTPUT_FORMAT)
        if last_output_format not in OUTPUT_FORMATS:
            last_output_format = DEFAULT_OUTPUT_FORMAT
        self.output_format_var = tk.StringVar(value=last_output_format)

        self.quality_key = self._normalize_quality_key(self.config_data.get("quality_preset", DEFAULT_QUALITY_KEY))
        self.quality_preset_var = tk.StringVar(value=self._quality_label(self.quality_key))

        last_concurrent_fragments = str(self.config_data.get("concurrent_fragments", DEFAULT_CONCURRENT_FRAGMENTS))
        if last_concurrent_fragments not in SPEED_PRESETS:
            last_concurrent_fragments = str(DEFAULT_CONCURRENT_FRAGMENTS)
        self.concurrent_fragments_var = tk.StringVar(value=last_concurrent_fragments)

        saved_output_template = self.config_data.get("output_template", DEFAULT_OUTPUT_TEMPLATE)
        if saved_output_template == LEGACY_DEFAULT_OUTPUT_TEMPLATE:
            saved_output_template = DEFAULT_OUTPUT_TEMPLATE
        self.output_template_var = tk.StringVar(value=saved_output_template)

        self._build_ui()
        self._apply_language()
        self.after(100, self._poll_log_queue)

    def _language(self):
        language = self.current_language
        if language not in SUPPORTED_LANGUAGES:
            return LANG_JA
        return language

    def _t(self, key):
        language = self._language()
        return TRANSLATIONS.get(language, TRANSLATIONS[LANG_JA]).get(key, TRANSLATIONS[LANG_JA][key])

    def _normalize_quality_key(self, value):
        if value in QUALITY_PRESETS:
            return value
        return QUALITY_LABEL_TO_KEY.get(value, DEFAULT_QUALITY_KEY)

    def _quality_label(self, quality_key=None):
        key = quality_key or self.quality_key
        labels = QUALITY_PRESET_LABELS.get(key, QUALITY_PRESET_LABELS[DEFAULT_QUALITY_KEY])
        return labels.get(self._language(), labels[LANG_JA])

    def _quality_values(self):
        return [self._quality_label(key) for key, _selector, _labels in QUALITY_PRESET_DEFS]

    def _on_quality_changed(self, _event=None):
        self.quality_key = self._normalize_quality_key(self.quality_preset_var.get())
        self.save_current_config()

    def _build_ui(self):
        self._build_menu()

        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)

        self.title_label = ttk.Label(outer, text=f"LiveCatch v{APP_VERSION}", font=("", 20, "bold"))
        self.title_label.pack(anchor="w")

        self.subtitle = ttk.Label(
            outer,
            text="",
            foreground="#555",
        )
        self.subtitle.pack(anchor="w", pady=(2, 14))

        self.mode_frame = ttk.LabelFrame(outer, text="", padding=10)
        self.mode_frame.pack(fill="x", pady=(0, 12))

        self.mode_reservation_radio = ttk.Radiobutton(
            self.mode_frame,
            text="",
            variable=self.mode_var,
            value=MODE_RESERVATION,
            command=self._on_mode_changed,
        )
        self.mode_reservation_radio.grid(row=0, column=0, sticky="w", pady=2)

        self.mode_live_full_radio = ttk.Radiobutton(
            self.mode_frame,
            text="",
            variable=self.mode_var,
            value=MODE_LIVE_FULL,
            command=self._on_mode_changed,
        )
        self.mode_live_full_radio.grid(row=1, column=0, sticky="w", pady=2)

        self.mode_catchup_stop_radio = ttk.Radiobutton(
            self.mode_frame,
            text="",
            variable=self.mode_var,
            value=MODE_CATCHUP_STOP,
            command=self._on_mode_changed,
        )
        self.mode_catchup_stop_radio.grid(row=2, column=0, sticky="w", pady=2)

        self.mode_hint = ttk.Label(self.mode_frame, text="", foreground="#666")
        self.mode_hint.grid(row=3, column=0, sticky="w", pady=(8, 0))

        form = ttk.Frame(outer)
        form.pack(fill="x")

        self.url_label, _url_entry = self._row(form, 0, "", self.url_var, width=92)

        save_row = ttk.Frame(form)
        save_row.grid(row=1, column=1, sticky="ew", pady=5)
        form.columnconfigure(1, weight=1)
        save_entry = ttk.Entry(save_row, textvariable=self.save_dir_var)
        save_entry.pack(side="left", fill="x", expand=True)
        self.choose_save_dir_btn = ttk.Button(save_row, text="", command=self._choose_save_dir)
        self.choose_save_dir_btn.pack(side="left", padx=(8, 0))
        self.save_dir_label = ttk.Label(form, text="")
        self.save_dir_label.grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)

        temp_row = ttk.Frame(form)
        temp_row.grid(row=2, column=1, sticky="ew", pady=5)
        self.use_temp_dir_check = ttk.Checkbutton(
            temp_row,
            text="",
            variable=self.use_temp_dir_var,
            command=self._on_temp_dir_toggled,
        )
        self.use_temp_dir_check.pack(side="left")
        self.temp_dir_entry = ttk.Entry(temp_row, textvariable=self.temp_dir_var)
        self.temp_dir_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.choose_temp_dir_btn = ttk.Button(temp_row, text="", command=self._choose_temp_dir)
        self.choose_temp_dir_btn.pack(side="left", padx=(8, 0))
        self.temp_dir_label = ttk.Label(form, text="")
        self.temp_dir_label.grid(row=2, column=0, sticky="w", padx=(0, 10), pady=5)

        self.output_template_label, _output_template_entry = self._row(form, 3, "", self.output_template_var, width=92)

        self.options_frame = ttk.LabelFrame(outer, text="", padding=10)
        self.options_frame.pack(fill="x", pady=(14, 10))

        self.wait_label = ttk.Label(self.options_frame, text="")
        self.wait_label.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)

        self.wait_entry = ttk.Entry(self.options_frame, textvariable=self.wait_var, width=8)
        self.wait_entry.grid(row=0, column=1, sticky="w", pady=4)

        self.speed_label = ttk.Label(self.options_frame, text="")
        self.speed_label.grid(row=0, column=2, sticky="e", padx=(28, 8), pady=4)
        self.speed_box = ttk.Combobox(self.options_frame, textvariable=self.concurrent_fragments_var, values=SPEED_PRESETS, width=8, state="readonly")
        self.speed_box.grid(row=0, column=3, sticky="w", pady=4)
        self.speed_box.bind("<<ComboboxSelected>>", lambda _event: self.save_current_config())

        self.quality_label = ttk.Label(self.options_frame, text="")
        self.quality_label.grid(row=0, column=4, sticky="e", padx=(28, 8), pady=4)
        self.quality_box = ttk.Combobox(self.options_frame, textvariable=self.quality_preset_var, values=self._quality_values(), width=34, state="readonly")
        self.quality_box.grid(row=0, column=5, sticky="w", pady=4)
        self.quality_box.bind("<<ComboboxSelected>>", self._on_quality_changed)

        self.output_format_label = ttk.Label(self.options_frame, text="")
        self.output_format_label.grid(row=1, column=4, sticky="e", padx=(28, 8), pady=4)
        self.output_format_box = ttk.Combobox(
            self.options_frame,
            textvariable=self.output_format_var,
            values=OUTPUT_FORMATS,
            width=12,
            state="readonly",
        )
        self.output_format_box.grid(row=1, column=5, sticky="w", pady=4)
        self.output_format_box.bind("<<ComboboxSelected>>", lambda _event: self.save_current_config())

        self.from_start_check = ttk.Checkbutton(
            self.options_frame,
            text="",
            variable=self.from_start_var,
        )
        self.from_start_check.grid(row=1, column=0, columnspan=4, sticky="w", pady=4)

        self.info_json_check = ttk.Checkbutton(self.options_frame, text="", variable=self.info_json_var)
        self.info_json_check.grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        self.metadata_check = ttk.Checkbutton(self.options_frame, text="", variable=self.metadata_var)
        self.metadata_check.grid(row=3, column=0, columnspan=3, sticky="w", pady=4)
        self.lightweight_catchup_check = ttk.Checkbutton(
            self.options_frame,
            text="",
            variable=self.lightweight_catchup_var,
            command=self.save_current_config,
        )
        self.lightweight_catchup_check.grid(row=4, column=0, columnspan=6, sticky="w", pady=4)

        self.cookies_check = ttk.Checkbutton(self.options_frame, text="", variable=self.cookies_var)
        self.cookies_check.grid(row=2, column=3, sticky="w", padx=(28, 8), pady=4)
        self.browser_label = ttk.Label(self.options_frame, text="")
        self.browser_label.grid(row=2, column=4, sticky="e", padx=(8, 6), pady=4)
        self.browser_box = ttk.Combobox(self.options_frame, textvariable=self.browser_var, values=["chrome", "edge", "firefox", "brave", "vivaldi", "opera"], width=12, state="readonly")
        self.browser_box.grid(row=2, column=5, sticky="w", pady=4)

        speed_hint = ttk.Label(
            self.options_frame,
            text="",
            foreground="#666",
        )
        self.speed_hint = speed_hint
        self.speed_hint.grid(row=5, column=0, columnspan=6, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(6, 10))

        self.start_btn = ttk.Button(buttons, text="", command=self.start_recording)
        self.start_btn.pack(side="left")

        self.stop_btn = ttk.Button(buttons, text="", command=self.stop_recording, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        self.show_command_btn = ttk.Button(buttons, text="", command=self.show_command)
        self.show_command_btn.pack(side="left", padx=(8, 0))
        self.check_tools_btn = ttk.Button(buttons, text="", command=self.check_tools)
        self.check_tools_btn.pack(side="left", padx=(8, 0))
        self.open_save_dir_btn = ttk.Button(buttons, text="", command=self.open_save_dir)
        self.open_save_dir_btn.pack(side="left", padx=(8, 0))

        self.status_frame = ttk.LabelFrame(outer, text="", padding=8)
        self.status_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(self.status_frame, height=18, wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(self.status_frame, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)

        self.help_label = ttk.Label(outer, text="", foreground="#666")
        self.help_label.pack(anchor="w", pady=(8, 0))

    def _build_menu(self):
        self.menu_bar = tk.Menu(self)
        self.language_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.language_menu.add_radiobutton(
            label="日本語",
            variable=self.language_var,
            value=LANG_JA,
            command=self._on_language_changed,
        )
        self.language_menu.add_radiobutton(
            label="English",
            variable=self.language_var,
            value=LANG_EN,
            command=self._on_language_changed,
        )
        self.menu_bar.add_cascade(label="言語", menu=self.language_menu)
        self.configure(menu=self.menu_bar)

    def _row(self, parent, row, label, var, width=60):
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        return label_widget, entry

    def _on_language_changed(self):
        language = self.language_var.get()
        if language not in SUPPORTED_LANGUAGES:
            language = LANG_JA
            self.language_var.set(language)
        self.current_language = language
        self.save_current_config()
        self._apply_language()

    def _apply_language(self):
        self.menu_bar.entryconfigure(0, label=self._t("menu_language"))
        self.language_menu.entryconfigure(0, label=self._t("menu_lang_ja"))
        self.language_menu.entryconfigure(1, label=self._t("menu_lang_en"))

        self.mode_frame.configure(text=self._t("mode_frame"))
        self.mode_reservation_radio.configure(text=self._t("mode_reservation_radio"))
        self.mode_live_full_radio.configure(text=self._t("mode_live_full_radio"))
        self.mode_catchup_stop_radio.configure(text=self._t("mode_catchup_stop_radio"))

        self.url_label.configure(text=self._t("url_label"))
        self.save_dir_label.configure(text=self._t("save_dir_label"))
        self.choose_save_dir_btn.configure(text=self._t("choose_save_dir"))
        self.temp_dir_label.configure(text=self._t("temp_dir_label"))
        self.use_temp_dir_check.configure(text=self._t("use_temp_dir"))
        self.choose_temp_dir_btn.configure(text=self._t("choose_temp_dir"))
        self.output_template_label.configure(text=self._t("output_template_label"))

        self.options_frame.configure(text=self._t("options_frame"))
        self.speed_label.configure(text=self._t("speed_label"))
        self.quality_label.configure(text=self._t("quality_label"))
        self.output_format_label.configure(text=self._t("output_format_label"))
        self.from_start_check.configure(text=self._t("from_start"))
        self.info_json_check.configure(text=self._t("write_info_json"))
        self.metadata_check.configure(text=self._t("embed_metadata"))
        self.lightweight_catchup_check.configure(text=self._t("lightweight_catchup"))
        self.cookies_check.configure(text=self._t("use_browser_cookies"))
        self.browser_label.configure(text=self._t("browser_label"))
        self.speed_hint.configure(text=self._t("speed_hint"))

        self.stop_btn.configure(text=self._t("stop"))
        self.show_command_btn.configure(text=self._t("show_command"))
        self.check_tools_btn.configure(text=self._t("check_tools"))
        self.open_save_dir_btn.configure(text=self._t("open_save_dir"))
        self.status_frame.configure(text=self._t("log_frame"))
        self.help_label.configure(text=self._t("help_text"))

        self.quality_box.configure(values=self._quality_values())
        self.quality_preset_var.set(self._quality_label())
        self._update_temp_dir_state()
        self._apply_mode_ui()

    def _on_mode_changed(self):
        self.save_current_config()
        self._apply_mode_ui()

    def _on_temp_dir_toggled(self):
        self._update_temp_dir_state()
        self.save_current_config()

    def _update_temp_dir_state(self):
        state = "normal" if self.use_temp_dir_var.get() else "disabled"
        self.temp_dir_entry.configure(state=state)
        self.choose_temp_dir_btn.configure(state=state)

    def _apply_mode_ui(self):
        mode = self.mode_var.get()

        if mode == MODE_RESERVATION:
            self.subtitle.configure(text=self._t("subtitle_reservation"))
            self.mode_hint.configure(text=self._t("hint_reservation"))
            self.start_btn.configure(text=self._t("start_reservation"))
            self.wait_label.configure(text=self._t("wait_label_reservation"))
            self.wait_entry.configure(state="normal")
            self.from_start_check.configure(state="normal")

        elif mode == MODE_LIVE_FULL:
            self.subtitle.configure(text=self._t("subtitle_live_full"))
            self.mode_hint.configure(text=self._t("hint_live_full"))
            self.start_btn.configure(text=self._t("start_live_full"))
            self.wait_label.configure(text=self._t("wait_label_unused"))
            self.wait_entry.configure(state="disabled")
            self.from_start_var.set(True)
            self.from_start_check.configure(state="disabled")

        elif mode == MODE_CATCHUP_STOP:
            self.subtitle.configure(text=self._t("subtitle_catchup_stop"))
            self.mode_hint.configure(text=self._t("hint_catchup_stop"))
            self.start_btn.configure(text=self._t("start_catchup_stop"))
            self.wait_label.configure(text=self._t("wait_label_unused"))
            self.wait_entry.configure(state="disabled")
            self.from_start_var.set(True)
            self.from_start_check.configure(state="disabled")

    def _choose_save_dir(self):
        d = filedialog.askdirectory(initialdir=self.save_dir_var.get() or str(Path.home()))
        if d:
            self.save_dir_var.set(d)
            self.save_current_config()

    def _choose_temp_dir(self):
        d = filedialog.askdirectory(initialdir=self.temp_dir_var.get() or str(Path.home()))
        if d:
            self.temp_dir_var.set(d)
            self.save_current_config()

    def check_tools(self):
        yt = find_executable("yt-dlp")
        ff = find_executable("ffmpeg")

        self._log(self._t("tools_check_header"))
        self._log(f"{self._t('app_folder')}: {app_dir()}\n")
        self._log(f"yt-dlp: {yt or self._t('not_found')}\n")
        self._log(f"ffmpeg: {ff or self._t('not_found')}\n\n")

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
            self._log(self._t("version_check_error").format(label=label, error=e))

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

    def _get_output_format(self):
        value = self.output_format_var.get()
        if value not in OUTPUT_FORMATS:
            value = DEFAULT_OUTPUT_FORMAT
            self.output_format_var.set(value)
        return value

    def _get_format_selector(self):
        self.quality_key = self._normalize_quality_key(self.quality_preset_var.get())
        return QUALITY_PRESETS.get(self.quality_key, QUALITY_PRESETS[DEFAULT_QUALITY_KEY])

    def _validate(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror(APP_NAME, self._t("err_url_required"))
            return False

        platform = detect_platform(url)
        if platform == PLATFORM_UNKNOWN:
            messagebox.showerror(APP_NAME, self._t("err_url_unsupported"))
            return False

        if self.use_temp_dir_var.get() and not self.temp_dir_var.get().strip():
            messagebox.showerror(APP_NAME, self._t("err_temp_dir_required"))
            return False

        if self.mode_var.get() == MODE_RESERVATION:
            try:
                wait = int(self.wait_var.get())
                if wait < 5:
                    raise ValueError
            except ValueError:
                messagebox.showerror(APP_NAME, self._t("err_wait_seconds"))
                return False

        if self._get_concurrent_fragments() >= 64:
            proceed = messagebox.askyesno(APP_NAME, self._t("high_speed_warning"))
            if not proceed:
                return False

        if find_executable("yt-dlp") is None:
            messagebox.showerror(APP_NAME, self._t("err_ytdlp_missing"))
            return False

        if find_executable("ffmpeg") is None:
            messagebox.showwarning(APP_NAME, self._t("warn_ffmpeg_missing"))

        return True

    def build_command(self):
        mode = self.mode_var.get()
        url = self.url_var.get().strip()
        if detect_platform(url) == PLATFORM_UNKNOWN:
            raise ValueError(self._t("err_url_unsupported"))

        save_dir = Path(self.save_dir_var.get()).expanduser()
        out_tmpl = self.output_template_var.get().strip() or DEFAULT_OUTPUT_TEMPLATE
        temp_dir = None
        if self.use_temp_dir_var.get():
            if not self.temp_dir_var.get().strip():
                raise ValueError(self._t("err_temp_dir_required"))
            temp_dir = Path(self.temp_dir_var.get()).expanduser()

        yt_dlp_path = find_executable("yt-dlp") or "yt-dlp"
        ffmpeg_path = find_executable("ffmpeg")
        ffmpeg_dir = str(Path(ffmpeg_path).parent) if ffmpeg_path else None

        concurrent_fragments = self._get_concurrent_fragments()
        format_selector = self._get_format_selector()
        output_format = self._get_output_format()

        cmd = [
            yt_dlp_path,
            "-N", str(concurrent_fragments),
            "-f", format_selector,
            "--merge-output-format", output_format,
            "--newline",
        ]

        if temp_dir:
            cmd.extend(["-P", f"home:{save_dir}", "-P", f"temp:{temp_dir}", "-o", out_tmpl])
        else:
            output_path = str(save_dir / out_tmpl)
            cmd.extend(["-o", output_path])

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
            raise ValueError(self._t("err_unknown_mode"))

        if self.info_json_var.get():
            cmd.append("--write-info-json")
        should_embed_metadata = self.metadata_var.get()
        if mode == MODE_CATCHUP_STOP and self.lightweight_catchup_var.get():
            should_embed_metadata = False

        if should_embed_metadata:
            cmd.append("--embed-metadata")
        if self.cookies_var.get():
            cmd.extend(["--cookies-from-browser", self.browser_var.get()])

        cmd.append(url)
        return cmd

    def save_current_config(self):
        try:
            wait_seconds = int(self.wait_var.get() or 30)
        except ValueError:
            wait_seconds = 30

        self.quality_key = self._normalize_quality_key(self.quality_preset_var.get())

        save_config({
            "version": APP_VERSION,
            "language": self._language(),
            "mode": self.mode_var.get(),
            "url": self.url_var.get(),
            "save_dir": self.save_dir_var.get(),
            "use_temp_dir": self.use_temp_dir_var.get(),
            "temp_dir": self.temp_dir_var.get(),
            "wait_seconds": wait_seconds,
            "cookies_from_browser": self.cookies_var.get(),
            "browser": self.browser_var.get(),
            "live_from_start": self.from_start_var.get(),
            "write_info_json": self.info_json_var.get(),
            "embed_metadata": self.metadata_var.get(),
            "lightweight_catchup_postprocess": self.lightweight_catchup_var.get(),
            "quality_preset": self.quality_key,
            "output_format": self._get_output_format(),
            "concurrent_fragments": self._get_concurrent_fragments(),
            "output_template": self.output_template_var.get(),
        })

    def show_command(self):
        try:
            platform = detect_platform(self.url_var.get())
            cmd = self.build_command()
            self._log(self._t("detected_platform").format(platform=platform_label(platform, self._language())))
            self._log(self._t("command_preview").format(command=subprocess.list2cmdline(cmd)))
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))

    def start_recording(self):
        if self.is_running:
            return
        if not self._validate():
            return

        self.save_current_config()
        Path(self.save_dir_var.get()).expanduser().mkdir(parents=True, exist_ok=True)
        if self.use_temp_dir_var.get():
            Path(self.temp_dir_var.get()).expanduser().mkdir(parents=True, exist_ok=True)

        self.run_started_at = time.time()
        self.catchup_frag_state = {}
        self.catchup_stop_sent = False
        self.catchup_candidate_since = None
        self.force_terminate_thread_started = False
        self.last_download_log_at = 0.0
        self.last_catchup_progress_log_at = 0.0
        self.catchup_progress_baseline = {}

        cmd = self.build_command()
        mode = self.mode_var.get()
        platform = detect_platform(self.url_var.get())
        mode_label = {
            MODE_RESERVATION: self._t("mode_label_reservation"),
            MODE_LIVE_FULL: self._t("mode_label_live_full"),
            MODE_CATCHUP_STOP: self._t("mode_label_catchup_stop"),
        }.get(mode, self._t("mode_label_default"))

        self._log(self._t("recording_started").format(mode_label=mode_label))
        self._log(self._t("platform_log").format(platform=platform_label(platform, self._language())))
        self._log(
            self._t("settings_log").format(
                quality=self.quality_preset_var.get(),
                output_format=self._get_output_format(),
                speed=self._get_concurrent_fragments(),
            )
        )
        if self.use_temp_dir_var.get():
            self._log(self._t("temp_dir_log").format(temp_dir=self.temp_dir_var.get()))
        if mode == MODE_CATCHUP_STOP:
            self._log(self._t("catchup_notice"))
            if self.lightweight_catchup_var.get() and self.metadata_var.get():
                self._log(self._t("lightweight_log"))
            if platform == PLATFORM_TWITCH:
                self._log(self._t("twitch_catchup_notice"))
        self._log(subprocess.list2cmdline(cmd) + "\n\n")

        self.is_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        t = threading.Thread(target=self._run_process, args=(cmd, mode), daemon=True)
        t.start()

    def _run_process(self, cmd, mode):
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
                self._maybe_stop_when_caught_up(line, mode)
                if self._should_display_log(line):
                    self.log_queue.put(line)

            code = self.proc.wait()
            self.log_queue.put(self._t("process_finished").format(code=code))
        except Exception as e:
            self.log_queue.put(self._t("process_error").format(error=e))
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

    def _maybe_stop_when_caught_up(self, line, mode):
        if mode != MODE_CATCHUP_STOP:
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

        self._log_catchup_progress(now)

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
                self.log_queue.put(self._t("caught_up_stop"))
                self._send_graceful_interrupt()
        else:
            self.catchup_candidate_since = None

    def _log_catchup_progress(self, now):
        if now - self.last_catchup_progress_log_at < CATCHUP_PROGRESS_LOG_INTERVAL_SEC:
            return

        active_items = [
            (stream_id, state)
            for stream_id, state in self.catchup_frag_state.items()
            if now - state["updated_at"] <= 10
        ]
        if not active_items:
            return

        parts = []
        for stream_id, state in active_items:
            current = state["current"]
            total = state["total"]
            remaining = max(total - current, 0)

            baseline = self.catchup_progress_baseline.get(stream_id)
            if baseline:
                prev_current, prev_time = baseline
                elapsed = max(now - prev_time, 0.001)
                rate = max(current - prev_current, 0) / elapsed
            else:
                rate = 0.0

            if rate > 0 and remaining > 0:
                eta_text = self._t("eta_remaining").format(duration=self._format_duration(remaining / rate))
            elif remaining == 0:
                eta_text = self._t("eta_near_live")
            else:
                eta_text = ""

            parts.append(self._t("catchup_part").format(
                stream_id=stream_id,
                current=current,
                total=total,
                remaining=remaining,
                rate=rate,
                eta=eta_text,
            ))
            self.catchup_progress_baseline[stream_id] = (current, now)

        self.last_catchup_progress_log_at = now
        self.log_queue.put(self._t("catchup_progress").format(parts=" | ".join(parts)))

    def _format_duration(self, seconds):
        seconds = max(int(seconds), 0)
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return self._t("hours_minutes").format(hours=hours, minutes=minutes)
        if minutes:
            return self._t("minutes_seconds").format(minutes=minutes, seconds=sec)
        return self._t("seconds").format(seconds=sec)

    def _send_graceful_interrupt(self):
        if not self.proc or self.proc.poll() is not None:
            return

        try:
            if os.name == "nt":
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.proc.send_signal(signal.SIGINT)
        except Exception as e:
            self.log_queue.put(self._t("interrupt_failed").format(error=e))
            try:
                self.proc.terminate()
            except Exception as e2:
                self.log_queue.put(self._t("terminate_failed").format(error=e2))

        if not self.force_terminate_thread_started:
            self.force_terminate_thread_started = True
            threading.Thread(target=self._force_terminate_later, daemon=True).start()

    def _force_terminate_later(self):
        time.sleep(45)
        if self.proc and self.proc.poll() is None:
            self.log_queue.put(self._t("force_terminate"))
            try:
                self.proc.terminate()
            except Exception as e:
                self.log_queue.put(self._t("force_terminate_failed").format(error=e))

    def stop_recording(self):
        if self.proc and self.proc.poll() is None:
            self._log(self._t("manual_stop"))
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
