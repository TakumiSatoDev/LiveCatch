# LiveCatch

**LiveCatch** is a simple Windows GUI app for recording YouTube and Twitch livestreams with `yt-dlp`.

配信待機枠の予約録画、配信中ライブの途中回収、そして「現在地点まで取得して停止」まで扱える、切り抜き師向けの軽量ツールです。URLから YouTube / Twitch を自動判定します。

## Features

- YouTube / Twitch ライブURLの自動判定
- YouTube / Twitch ライブの予約録画
- 配信中ライブを開始地点から最後まで録画
- 配信中ライブを開始地点から現在地点まで取得して停止
- 日本語 / English の言語切り替えメニュー
- 最後に使ったモードを保存
- 保存先フォルダ指定
- 高速一時保存先指定
- ブラウザCookie対応
- 速度設定：同時fragment数をプルダウンで変更
- 画質設定：追いつき優先 / 最高画質 / 1440p / 1080p / 720p / 480p / 360p をプルダウンで変更
- 保存形式：mp4 / mkv / webm を選択
- 「現在まで取得」モード向けの後処理軽量化
- `winget` 不要のローカルツール導入
- `yt-dlp` / `ffmpeg` を `tools` フォルダから自動検出
- Windows向け exe 化対応

## Modes

### 1. 予約録画

配信待機枠URLを入れておくと、開始まで待機して録画します。

```text
未開始 → 開始まで待機 → 配信開始で録画 → 配信終了で保存
```

### 2. 配信中ライブを最後まで録画

すでに配信中のライブURLを入れると、DVRやライブ履歴が取得できる場合に開始地点から取得を試します。

```text
ライブ開始地点 → 現在地点に追いつく → そのまま配信終了まで録画
```

途中からでも最初から最後まで録画したい時用です。

### 3. 配信中ライブを現在まで取得

すでに配信中のライブURLを入れると、DVRやライブ履歴が取得できる場合に開始地点から取得を試します。

```text
ライブ開始地点 → 現在地点に追いつく → 停止
```

切り抜き用に「開始から今まで」だけ欲しい時用です。

## Speed setting

LiveCatchの「速度」は、ディスクへの書き込み速度を直接変える設定ではありません。

内部的には `yt-dlp` の `-N` / `--concurrent-fragments` に相当する設定で、DVRやライブ履歴に溜まっている過去部分のfragmentを同時にいくつ取得するかを指定します。

```text
同時fragment数 1   = 安定重視
同時fragment数 8   = 初期値・おすすめ
同時fragment数 16  = 高速寄り
同時fragment数 32  = 強め
同時fragment数 64+ = 実験寄り
```

速くできるのは、すでに配信済みでDVRやライブ履歴に溜まっている過去部分だけです。現在以降の未来部分は、配信が進む速度以上には取得できません。

追いつきが遅い場合は、速度を上げるだけでなく画質を下げる方が効くことがあります。特に 1080p60 や 1440p 以上のライブは取得量が大きいため、まずは「追いつき優先：720p30以下」と速度 16〜32 の組み合わせを試してください。

## Temporary folder and format

保存先が HDD、外付けドライブ、NAS、同期フォルダの場合は、「高速一時保存先を使う」を有効にして、一時保存先を内蔵SSD/NVMeにしてください。

内部的には `yt-dlp` の `-P home:<保存先>` と `-P temp:<一時保存先>` を使います。fragmentや中間ファイルは高速な一時保存先に置き、最終ファイルは通常の保存先へ出力します。

保存形式は以下から選べます。

```text
mp4
mkv
webm
```

初期値は **mp4** です。互換性重視なら mp4、長時間ライブや後処理の安定性重視なら mkv も選択肢です。

「現在まで取得」モードでは、後処理軽量化を有効にするとメタデータ埋め込みを省略します。追いつき速度や終了処理を優先したい場合に使ってください。

## Quality setting

画質はプルダウンから選べます。

```text
おすすめ：1080p以下
追いつき優先：720p30以下
追いつき優先：480p30以下
最高画質
1440p以下
1080p以下
720p以下
480p以下
360p以下
```

初期値は **おすすめ：1080p以下** です。

理由は、切り抜き用途で扱いやすく、速度・容量・画質のバランスが良いためです。

## Recommended settings by environment

### 低スペックPC / Wi-Fi環境

想定：

```text
CPU: 古めのCore i3 / Ryzen 3 相当
RAM: 8GB
保存先: HDD or 遅めのSSD
回線: Wi-Fi / 100Mbps未満
```

おすすめ：

```text
速度: 4
画質: 720p以下
保存先: SSD推奨
```

安定優先です。HDD保存の場合は、長時間配信で重くなる可能性があります。

### 標準PC / 一般的な光回線

想定：

```text
CPU: Core i5 / Ryzen 5 相当
RAM: 16GB
保存先: SSD
回線: 光回線 / 実効100〜300Mbps程度
```

おすすめ：

```text
速度: 8
画質: おすすめ：1080p以下
保存先: SSD
```

LiveCatchの初期設定です。まずはここから試してください。

### 高性能PC / 有線LAN

想定：

```text
CPU: Core i7 / Ryzen 7 相当以上
RAM: 16GB〜32GB
保存先: NVMe SSD
回線: 有線LAN / 実効300Mbps以上
```

おすすめ：

```text
速度: 16
画質: 1080p以下 or 1440p以下
保存先: NVMe SSD
```

DVRの過去部分をかなり速く回収できます。失敗が増える場合は速度を8に下げてください。

### かなり強い環境 / 動作テスト用

想定：

```text
CPU: 高性能CPU
RAM: 32GB以上
保存先: 高速NVMe SSD
回線: 実効500Mbps〜1Gbps級
```

おすすめ：

```text
速度: 32
画質: 最高画質 or 1440p以下
保存先: NVMe SSD
```

これは高速回収テスト向けです。配信サイト側やネットワーク状況によっては失敗・速度低下・一時的な制限が起きる場合があります。

### 実験設定

```text
速度: 64 or 128
画質: 最高画質
```

かなり攻めた設定です。回線や配信サイト側に負荷がかかり、fragment失敗が増える可能性があります。普段使いではおすすめしません。

## Requirements

- Windows
- Python 3.11+
- Internet connection for first tool setup

`yt-dlp` and `ffmpeg` are installed locally into the `tools` folder by `install_tools.ps1`.

## Quick Start

PowerShellをこのフォルダで開いて実行します。

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install_tools.ps1
.\run_app.bat
```

## Build EXE

```powershell
.\build_exe.bat
```

`tools` が存在しない場合、`build_exe.bat` が自動で `install_tools.ps1` を実行します。

成功すると以下の構成になります。

```text
dist/
├─ LiveCatch.exe
└─ tools/
   ├─ yt-dlp.exe
   ├─ ffmpeg.exe
   └─ ffprobe.exe
```

## Config

設定は以下に保存されます。

```text
C:\Users\<ユーザー名>\.livecatch_config.json
```

保存される主な設定：

- 最後に使ったモード
- 言語設定
- URL
- 保存先
- 一時保存先
- Cookie設定
- 速度設定
- 画質設定
- 保存形式
- 後処理軽量化設定
- 出力テンプレート

初期出力テンプレート：

```text
%(extractor_key)s/%(upload_date)s_%(channel)s_%(title)s/%(upload_date)s_%(title)s.%(ext)s
```

`%(extractor_key)s` により、YouTube と Twitch は保存先フォルダ内で自動的に分かれます。既存設定が旧デフォルトのままの場合は、この新しい初期テンプレートへ自動移行します。

## Notes

- DVRやライブ履歴が取得できないライブは、ライブ先頭から取得できない場合があります。
- 「現在まで取得」モードは、`yt-dlp` の fragment ログを見て追いつき判定します。
- Twitchの追いつき停止は、`yt-dlp` が fragment の現在値と総数を出力できる場合に動作します。
- YouTube、Twitch、`yt-dlp` 側の出力仕様が変わると調整が必要になる場合があります。
- 配信者・権利者の許可がある用途で使用してください。


## v2.1.3 notes

This release expands LiveCatch while preserving the existing recording modes:

- YouTube / Twitch URL auto-detection.
- Japanese / English language menu.
- Output format selection: mp4 / mkv / webm.
- Fast temporary folder support.
- Lightweight post-processing option for download-up-to-now mode.
- Catch-up progress logging and catch-up priority quality presets.

---

# English

**LiveCatch** is a lightweight Windows GUI app for recording YouTube and Twitch livestreams with `yt-dlp`.

It supports reservation recording, catching up active livestreams from the beginning, and stopping once the download reaches the current live point. YouTube and Twitch URLs are detected automatically.

## Features

- Automatic YouTube / Twitch URL detection
- Reservation recording for YouTube / Twitch livestreams
- Record an active livestream from the beginning to the end
- Download an active livestream from the beginning up to the current live point, then stop
- Japanese / English language menu
- Save folder selection
- Fast temporary folder support
- Browser cookie support
- Speed setting based on concurrent fragment downloads
- Quality presets including catch-up priority options
- Output format selection: mp4 / mkv / webm
- Lightweight post-processing option for download-up-to-now mode
- Local `yt-dlp` / `ffmpeg` detection from the `tools` folder
- Windows exe build support

## Modes

### 1. Reservation Recording

Enter a scheduled stream URL and LiveCatch waits until the stream starts, then records until it ends.

```text
Not started -> wait -> record when live -> save when ended
```

### 2. Record Active Livestream To The End

For an already-live URL, LiveCatch tries to download from the beginning when DVR or live history is available, catches up to the current live point, then continues recording until the stream ends.

```text
Stream start -> catch up to now -> keep recording until stream ends
```

### 3. Download Active Livestream Up To Now

For clipping workflows, LiveCatch tries to download from the beginning and stops after it catches up to the current live point.

```text
Stream start -> catch up to now -> stop
```

This stop behavior depends on `yt-dlp` fragment logs. For Twitch, it works when `yt-dlp` outputs current and total fragment values.

## Speed And Quality

The speed setting maps to `yt-dlp -N / --concurrent-fragments`. It speeds up already-buffered DVR or live-history content only. Future live content cannot be downloaded faster than real time.

If catch-up is slow, lowering quality often helps more than raising the fragment count. Try:

```text
Quality: Catch-up priority: 720p30 or lower
Speed: 16 or 32 concurrent fragments
```

## Temporary Folder And Format

If your final save folder is on an HDD, external drive, NAS, or synced folder, enable the fast temporary folder option and point it to an internal SSD/NVMe.

Internally, LiveCatch passes these paths to `yt-dlp`:

```text
-P home:<final save folder>
-P temp:<temporary folder>
```

Fragments and intermediary files are written to the temporary folder, while the final recording is placed in the normal save folder.

Available output formats:

```text
mp4
mkv
webm
```

The default is **mp4**. Use mp4 for compatibility. For long livestreams or safer post-processing, mkv can be a practical choice.

In download-up-to-now mode, the lightweight post-processing option skips metadata embedding. Use it when catch-up speed and faster finishing matter more than embedded metadata.

## Language

Use the app menu:

```text
Language -> Japanese / English
```

The selected language is saved in:

```text
C:\Users\<username>\.livecatch_config.json
```

## Quick Start

Open PowerShell in this folder and run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install_tools.ps1
.\run_app.bat
```

## Build EXE

```powershell
.\build_exe.bat
```

Expected output:

```text
dist/
├─ LiveCatch.exe
└─ tools/
   ├─ yt-dlp.exe
   ├─ ffmpeg.exe
   └─ ffprobe.exe
```

## Notes

- Streams without DVR or accessible live history may not be downloadable from the beginning.
- The "download up to now" mode depends on `yt-dlp` fragment log output.
- YouTube, Twitch, or `yt-dlp` output changes may require future adjustments.
- Use LiveCatch only for recordings you are authorized to make.
