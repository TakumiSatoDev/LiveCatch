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

## 文字化けしたとき

LiveCatchの設定ファイルとGUI / Worker間のイベントはUTF-8前提です。
**GUIは正常なのにPowerShellやCMDの日本語だけ崩れる場合は、録画データではなく端末側の文字コード表示が原因のことが多いです。**

ソース版をWindows Terminal / PowerShellから起動する場合は、まず次を試してください。

```powershell
chcp 65001 > $null
$env:PYTHONUTF8="1"
$env:PYTHONIOENCODING="utf-8"
python livecatch.py
```

CMDの場合:

```bat
chcp 65001
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python livecatch.py
```

新しいWindows版は起動時にUTF-8のI/O環境を設定し、GUI文字列についても典型的な **UTF-8をCP932として読んだ文字化け**を検出した場合は復元を試みます。さらにReleaseビルドでは、完成した `LiveCatch.exe` 自身を起動して「録画 / 設定 / 自動録画 / 開始」の日本語がTk上で正しく往復することを確認し、失敗したビルドはReleaseへ進めません。

端末ログだけが崩れる古い版やソース版では、上記の `chcp 65001` / `PYTHONUTF8` / `PYTHONIOENCODING` も引き続き有効です。古い `cmd.exe` よりWindows Terminal / PowerShell 7の使用を推奨します。

- GUIと `LiveCatchWorker.exe` は**同じリリースの組み合わせ**を使ってください。古いWorkerと新しいGUIを混在させないでください。
- `.livecatch*.json` などの設定ファイルを手動編集する場合は **UTF-8** で保存し、Shift-JIS / CP932へ変換しないでください。
- エクスプローラーでは正常なファイル名が古いCMDだけで崩れる場合は、ファイル自体ではなく端末のコードページやフォント側を確認してください。
- GUIログにすでに `�` が入っている場合、その文字はデコード時に置換済みなので元の文字へは戻せません。最新版のGUI / Workerへ揃えて再起動してください。

## 録画モード

| 設定 | 動作 |
|---|---|
| `reservation` | yt-dlpが認識する配信待機枠を待って録画。Twitchの任意のオフラインチャンネルを常時監視する機能ではありません。 |
| `live_full` | 対応するDVRの先頭から回収し、配信終了まで継続。サイトが公開していない過去部分は回収できません。 |
| `catchup_stop` | YouTube DVRで最初に観測した音声・映像の共通fragment終端まで回収して正常終了。ボタンを押した厳密な時刻でも、追いついた時の最新地点でもありません。Twitchのライブでは推測せずエラーにします。終了済みVODは通常の有限取得です。 |

保存先、一時保存先、画質、mp4/mkv/webm、Cookie、メタデータ、info.json、日本語/Englishを維持しています。
コンテナだけ変更できないコーデックの組合せはFFmpegエラーになります。自動で画質を落として成功扱いにはしません。

### 保存フォルダ

既定の保存構成は手動録画も自動録画も**チャンネル単位**で整理します。

```text
LiveCatch/
├─ YouTube/
│  └─ <channel>/
│     └─ <date>_<video-id>_<title>/
└─ Twitch/
   └─ <channel>/
      └─ <date>_<stream-id>_<title>/
```

手動録画の旧既定テンプレートは読み込み時にこのチャンネル別構成へ自動移行します。
自分で変更した出力テンプレートは上書きしません。自動録画は従来どおりチャンネルごとのフォルダへ保存します。

## 自動録画・追いつき・監視専用プリセット

「YouTube / Twitch 自動録画」では、**監視中でもチャンネルを追加**できます。追加したチャンネルは次の定期巡回を待たずすぐ確認し、すでに配信中なら同時録画上限に空きがある時点で録画を開始します。

`from_start`（既定）は、途中から登録・検知した配信でもサービス側が公開している範囲を可能な限り先頭から追いつきます。YouTubeではDVR、Twitchではyt-dlpが現在配信に紐づく成長中VODを確認します。Twitchで対応VODが取れない場合はライブ端へフォールバックします。配信サービスが過去部分を公開していない場合、その部分を復元することはできません。`live_edge` を選ぶと従来どおり検知時点付近から録画します。

監視録画の高負荷パラメータは手動録画から分離し、プリセットで選べます。保存先・画質・Cookie・保存形式・GPU変換モードなどは「設定」タブの共通値を使い、下表の項目だけ監視専用値で上書きします。

| 監視プリセット | fragment並列 | prefetch | GPU jobs | NVENC preset | 用途 |
|---|---:|---:|---:|---|---|
| `manual` | 手動録画設定を使用 | 手動録画設定を使用 | 手動録画設定を使用 | 手動録画設定を使用 | 旧設定互換・完全手動 |
| `balanced` | 32 | 3 | 2 | balanced | 複数録画の安定性重視 |
| `fast` | 64 | 4 | 3 | fast | **新規設定の既定**。高性能PC向け |
| `extreme` | 96 | 6 | 4 | fast | 1Gbps級＋NVMe向け |
| `max` | 128 | 8 | 6 | max_speed | 実測前提の最大負荷 |

設定画面は **「録画 / 設定 / YouTube・Twitch自動録画」** を中心に整理し、従来の「詳細」「GPU変換」「バックグラウンド」に分散していた項目を「設定」へまとめています。バックグラウンド・Windowsログイン起動も同じ設定タブから変更できます。
## 進行状況とアップデート通知

録画中はGUIの「進行状況」に、取得情報の解析、録画、結合、変換の段階をステップ表示します。取得できる場合は進捗バー、fragment数・バイト数・速度、保存済みファイル数、変換の `n / total` を表示します。**開始からの経過時間**もリアルタイム表示し、終了後は確定した**所要時間**を残します。自動録画一覧にもチャンネルごとの時間列を表示します。

起動時と6時間ごとにGitHub Releaseのバージョンをバックグラウンド確認します。GUI上部の「更新を確認」ボタンでも手動確認できます。「更新版があります」と表示されたら「更新する」を押すと、更新パッケージをアプリ内で取得し、録画停止後に既存のインストール先へ実行ファイルと同梱ツールを差し替えて再起動します。ブラウザやインストーラー画面は開きません。通信できない場合も録画機能には影響しません。更新版を出すときは `livecatch_core/__init__.py` の `__version__` を上げ、同じ番号のタグを作成してください。

## 取得エンジンを内部から変更

`bounded`（既定）はyt-dlp **2026.08.19 / PyPI 2026.8.19** の `FragmentFD` を、録画子プロセス内だけで変更します。
HTTP取得・再試行・復号・resume・結合は上流に任せ、先読み／順序付き保存／snapshot終端／停止を変更します。
`site-packages` や上流リポジトリのファイルは書き換えません。終了時にメソッドも元に戻します。

* ライブfragment生成器を別スレッドで進め、未来のfragment待ちが既取得分の保存を塞がない構造。
* 完了済み未保存分も含む先読み上限。初期値8並列×2。通常域は1〜32並列、実験用に最大128並列・prefetch倍率8まで選択可能。
* 保存順は元の順序。resume checkpointも保存するfragment番号に固定。
* 取得不可fragmentは黙って飛ばさず失敗。再試行回数・待ち時間は上限付き。

**バージョンが違えば内部パッチを拒否**します。無検証で最新版へ適用しません。
`stock` は内部fragmentパッチを外す比較・復旧用です。snapshotは使用できません。
停止を受け付けない外部プロセスは「強制停止」で明示的に終了できますが、中間ファイルが残る場合があります。
通常停止に自動45秒強制終了はありません。長いmux処理を勝手に切断しません。

## GPU変換

**GPUでインターネット回線を速くする仕組みではありません。**
通常録画はストリームコピー中心です。**録画するだけなら `off` が推奨**で、これが最速かつ再エンコードによる画質劣化もありません。
GPU変換は、H.264/MP4への統一、解像度の縮小、容量削減、編集ソフトや端末との互換性確保が必要な場合だけ使います。
取得後、元動画を残したまま別の `.export.mp4` を生成します。

| モード | 動作 |
|---|---|
| `off` | 追加変換なし。既定値。 |
| `cuda` | GPUデコード → CUDA `scale_cuda` → `h264_nvenc`。GPUが使えなければ失敗を明示。 |
| `auto` | CUDA実動作プローブ後に選択。GPU失敗時はCPUへフォールバック。 |
| `cpu` | `libx264` によるCPU変換。 |

NVDEC/NVENCは専用動画エンジン、CUDAはここでは拡縮処理です。独自CUDAカーネルを実装したわけではありません。
最大8ファイルを並列変換できます。1つの動画を時間分割して並列再結合する方式ではありません。
NVENC速度プリセットは balanced=p4 / fast=p2 / max_speed=p1 を選択できます。
GPUセッション数／VRAM／ディスクの上限があるため、同時数を増やせば必ず速くなるとは限りません。

```powershell
python -m livecatch_core export "recording1.mkv" "recording2.mkv" --mode cuda --height 720 --jobs 2 --preset fast
```

### 実験的な高負荷設定

高速回線・NVMe・十分なCPU/GPU余力がある環境向けに、GUIから `64 / 96 / 128` fragment並列、
prefetch倍率 `5〜8`、GPU変換 `5〜8` ジョブ、NVENC `max_speed (p1)` を選べます。
これらは速度保証ではありません。CDNや配信サービス側の制限、429、ソケット数、CPU、NVMe、
アンチウイルス、VRAM/NVENCセッション上限が先に詰まると、32並列以下より遅くなることがあります。
高負荷域では開始前に確認ダイアログを表示します。

## PCスペック別おすすめ設定

LiveCatchは比較的高い並列数まで動かせる設計なので、下表は**やや攻め寄りの開始点**です。
実効速度はPC性能だけでなく、YouTube / Twitch / CDN、回線混雑、配信側のfragment構成、
ストレージ、セキュリティソフトなどで先に頭打ちになることがあります。

| クラス | PC / 回線の目安 | fragment並列 | prefetch | GPU変換 | GPU jobs / preset | 自動録画上限の目安 |
|---|---|---:|---:|---|---|---:|
| 軽量 | 4コア級 / 8GB / SATA SSD / ～100Mbps | 8～16 | 2 | `off` 推奨 | 1 / balanced | 1～2 |
| 標準 | 6～8コア級 / 16GB / NVMe / 300～500Mbps | 32 | 3～4 | 必要時 `auto` | 1～2 / balanced～fast | 2～3 |
| 高性能 | 8～16コア級 / 32GB / NVMe / 1Gbps / RTX 20～40級 | 64～96 | 4～6 | `auto` / `cuda` | 2～4 / fast | 3～5 |
| Extreme | 12コア以上 / 32～64GB+ / 高速NVMe / 1～2.5GbE以上 | 96～128 | 6～8 | `cuda` | 4～8 / fast～max_speed | 4～8を実測で調整 |

### 攻めるときの上げ方

単体録画なら、まず `64 / prefetch 4`、余裕があれば `96 / 6`、さらに回線・NVMe・CPUに余裕があるなら
`128 / 8` まで上げて比較してください。**最高値が最速とは限らない**ので、実効Mbpsや完了時間で判断します。

高性能GPUで変換を同時に回す場合は `gpu_jobs=2～4` から始め、GPU使用率・VRAM・Video Encode / Decode負荷に
余裕があるなら `4～8` へ上げます。速度優先なら `fast (p2)`、とにかく処理時間優先なら
`max_speed (p1)` が候補です。なお、`gpu_jobs` は**複数ファイルの同時変換数**であり、
1本の動画を8分割して単体変換を8倍速にする設定ではありません。

自動録画では設定が**録画1本ごと**に適用されます。たとえば4本同時に各96並列で動かすと、
単体96並列より大きな回線・ソケット・ディスク負荷になります。複数同時録画では
`32～64 / prefetch 3～4` から始め、余裕を確認して `64～96` へ上げるのがおすすめです。

### どこが詰まっているかの見分け方

- 回線使用率が低くCPUも余っている → fragment並列 / prefetchを上げる余地あり。
- 回線が上限付近 → 並列数を増やしてもほぼ速くなりません。
- CPU使用率やコンテキストスイッチが張り付く → fragment並列を一段下げる。
- NVMe使用率や書き込み待ちが高い → prefetchや同時録画数を下げる。
- 429 / timeout / 接続リセットが増える → CDN側制限の可能性があるので並列数を下げる。
- GPU変換でVideo Encode / Decodeが張り付く → `gpu_jobs` を増やしても改善しない可能性が高い。
- GPU使用率が低くVideo Encode / Decodeにも余裕がある → `gpu_jobs` を増やす余地あり。

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

## リリース版インストーラー

リリース版はGitHub ActionsでWindows用のセットアップ.exeを作成します。`__version__` と同じバージョンのタグをmainに付けると、テスト、PyInstaller、Inno Setupを実行してGitHub Releaseへ `LiveCatch-Setup-X.Y.Z.exe` を添付します。

ローカルで作成する場合は、Inno Setup 6をインストールしたうえで次を実行します。

```powershell
.\build_release.bat 3.2.0
```

インストーラーにはLiveCatch本体、録画ワーカー、FFmpeg、ffprobe、Deno、ライセンス通知を含めます。設定ファイルはユーザープロファイルに残るため、更新インストールで設定を削除しません。

実行済みテスト・未検証項目は [docs/VALIDATION.md](docs/VALIDATION.md)、内部変更根拠は [docs/UPSTREAM_AUDIT.md](docs/UPSTREAM_AUDIT.md) を参照してください。
配信の保存は権利・アクセス許可がある対象で利用してください。DRM・認証・アクセス制限を回避する機能はありません。
