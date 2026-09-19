# LiveCatch 3 — bounded recording + optional CUDA export

**開発版。mainへの即マージではなく、実機検証用のリファクタリングです。**
YouTube / Twitch の録画GUIを、設定・GUI・録画ワーカー・yt-dlp内部アダプター・動画変換に分離しました。
従来版はこのブランチの `legacy_livecatch.py` と `docs/README-v2.md` に保存しています。

## 起動

Python 3.10以上を使用します。既存の `tools/yt-dlp.exe` ではなく、**固定版のPythonパッケージ**が新エンジンの依存先です。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m livecatch_core diagnose
python livecatch.py
```

`ffmpeg` と `ffprobe` の両方を `tools/` またはPATHに置いてください。
既存の `install_tools.ps1` はFFmpeg類の導入に使えますが、それだけではv3のPython依存は入りません。
YouTube用にDenoもPATHまたは `tools/deno.exe` に用意してください。診断で存在を確認できます。
CUDAを使わない録画はNVIDIA GPUなしでも動く設計です。

## 録画モード

| 設定 | 動作 |
|---|---|
| `reservation` | yt-dlpが認識する配信待機枠を待って録画。Twitchの任意のオフラインチャンネルを常時監視する機能ではありません。 |
| `live_full` | 対応するDVRの先頭から回収し、配信終了まで継続。サイトが公開していない過去部分は回収できません。 |
| `catchup_stop` | YouTube DVRで最初に観測した音声・映像の共通fragment終端まで回収して正常終了。ボタンを押した厳密な時刻でも、追いついた時の最新地点でもありません。Twitchのライブでは推測せずエラーにします。終了済みVODは通常の有限取得です。 |

保存先、一時保存先、画質、mp4/mkv/webm、Cookie、メタデータ、info.json、日本語/Englishを維持しています。
コンテナだけ変更できないコーデックの組合せはFFmpegエラーになります。自動で画質を落として成功扱いにはしません。

## 取得エンジンを内部から変更

`bounded`（既定）はyt-dlp **2026.08.19 / PyPI 2026.8.19** の `FragmentFD` を、録画子プロセス内だけで変更します。
HTTP取得・再試行・復号・resume・結合は上流に任せ、先読み／順序付き保存／snapshot終端／停止を変更します。
`site-packages` や上流リポジトリのファイルは書き換えません。終了時にメソッドも元に戻します。

* ライブfragment生成器を別スレッドで進め、未来のfragment待ちが既取得分の保存を塞がない構造。
* 完了済み未保存分も含む先読み上限。初期値8並列×2、HTTP並列数は1〜32まで。
* 保存順は元の順序。resume checkpointも保存するfragment番号に固定。
* 取得不可fragmentは黙って飛ばさず失敗。再試行回数・待ち時間は上限付き。

**バージョンが違えば内部パッチを拒否**します。無検証で最新版へ適用しません。
`stock` は内部fragmentパッチを外す比較・復旧用です。snapshotは使用できません。
停止を受け付けない外部プロセスは「強制停止」で明示的に終了できますが、中間ファイルが残る場合があります。
通常停止に自動45秒強制終了はありません。長いmux処理を勝手に切断しません。

## GPU変換

**GPUでインターネット回線を速くする仕組みではありません。**
通常録画はストリームコピー中心です。最速の無劣化保存ではGPU変換を `off` のままにします。
取得後、必要な時だけ別の `.export.mp4` を生成します。

| モード | 動作 |
|---|---|
| `off` | 追加変換なし。既定値。 |
| `cuda` | GPUデコード → CUDA `scale_cuda` → `h264_nvenc`。GPUが使えなければ失敗を明示。 |
| `auto` | CUDA実動作プローブ後に選択。GPU失敗時はCPUへフォールバック。 |
| `cpu` | `libx264` によるCPU変換。 |

NVDEC/NVENCは専用動画エンジン、CUDAはここでは拡縮処理です。独自CUDAカーネルを実装したわけではありません。
最大4ファイルを並列変換できます。1つの動画を時間分割して並列再結合する方式ではありません。
GPUセッション数／VRAM／ディスクの上限があるため、同時数を増やせば必ず速くなるとは限りません。

```powershell
python -m livecatch_core export "recording1.mkv" "recording2.mkv" --mode cuda --height 720 --jobs 2
```

入力ファイルは上書きしません。出力も既存ファイルを上書きしません。MP4変換は**非可逆圧縮**です。
HDRは明示的なトーンマッピングが未実装のため拒否します。
出力公開は同一フォルダのhard linkで原子的に行います。NTFS等を使用してください。
exFATや一部NASなどhard link非対応の保存先では、既存ファイルを守るため失敗します。
この制約は追加GPU/CPU変換に対するもので、通常録画の保存先制限ではありません。

## 開発・検証

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python benchmarks/loopback.py
```

`tests/test_ytdlp_integration.py` は実yt-dlp＋ローカルHTTPで順序付き取得と音声・映像snapshotを検証します。
依存がない環境では明示的にskipします。外部サイト、Cookie、GPUは使いません。
Windowsのexeは `build_exe.bat` でGUIと専用ワーカーを別々に作ります。
`LiveCatch.exe`、`LiveCatchWorker.exe`、`tools/` を一緒に配置してください。

実行済みテスト・未検証項目は [docs/VALIDATION.md](docs/VALIDATION.md)、内部変更根拠は [docs/UPSTREAM_AUDIT.md](docs/UPSTREAM_AUDIT.md) を参照してください。
配信の保存は権利・アクセス許可がある対象で利用してください。DRM・認証・アクセス制限を回避する機能はありません。
