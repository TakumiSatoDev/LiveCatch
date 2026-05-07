# LiveCatch

**LiveCatch** is a simple Windows GUI app for recording YouTube and Twitch livestreams with `yt-dlp`.

配信待機枠の予約録画、配信中ライブの途中回収、そして「現在地点まで取得して停止」まで扱える、切り抜き師向けの軽量ツールです。URLから YouTube / Twitch を自動判定します。

## Features

- YouTube / Twitch ライブURLの自動判定
- YouTube / Twitch ライブの予約録画
- 配信中ライブを開始地点から最後まで録画
- 配信中ライブを開始地点から現在地点まで取得して停止
- 最後に使ったモードを保存
- 保存先フォルダ指定
- ブラウザCookie対応
- 速度設定：同時fragment数をプルダウンで変更
- 画質設定：追いつき優先 / 最高画質 / 1440p / 1080p / 720p / 480p / 360p をプルダウンで変更
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
- URL
- 保存先
- Cookie設定
- 速度設定
- 画質設定
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


## v1.1.1 maintenance notes

This release keeps the v1.1.0 feature set and focuses on safety/cleanup:

- The GUI log now trims old lines automatically so long downloads do not keep growing memory usage.
- High-frequency download progress logs are throttled in the GUI while the internal catch-up detector still reads every line.
- Repeated stop requests no longer spawn multiple force-terminate watcher threads.
- `install_tools.ps1` skips downloading tools that already exist, while still copying them into `dist/tools` when needed.
- `build_exe.bat` uses PyInstaller `--clean` to reduce stale build-cache issues.
