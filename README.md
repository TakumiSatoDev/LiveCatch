# LiveCatch

**LiveCatch** is a simple Windows GUI app for recording YouTube livestreams with `yt-dlp`.

配信待機枠の予約録画、配信中ライブの途中回収、そして「現在地点まで取得して停止」まで扱える、切り抜き師向けの軽量ツールです。

## Features

- YouTubeライブの予約録画
- 配信中ライブを開始地点から最後まで録画
- 配信中ライブを開始地点から現在地点まで取得して停止
- 最後に使ったモードを保存
- 保存先フォルダ指定
- ブラウザCookie対応
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

すでに配信中のライブURLを入れると、DVRが有効な場合に開始地点から取得を試します。

```text
ライブ開始地点 → 現在地点に追いつく → そのまま配信終了まで録画
```

途中からでも最初から最後まで録画したい時用です。

### 3. 配信中ライブを現在まで取得

すでに配信中のライブURLを入れると、DVRが有効な場合に開始地点から取得を試します。

```text
ライブ開始地点 → 現在地点に追いつく → 停止
```

切り抜き用に「開始から今まで」だけ欲しい時用です。

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
- 出力テンプレート
- フォーマット設定

## Notes

- DVRが無効のライブは、ライブ先頭から取得できない場合があります。
- 「現在まで取得」モードは、`yt-dlp` の fragment ログを見て追いつき判定します。
- YouTubeや `yt-dlp` 側の出力仕様が変わると調整が必要になる場合があります。
- 配信者・権利者の許可がある用途で使用してください。
