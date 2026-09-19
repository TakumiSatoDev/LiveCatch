# 検証状況（2026-09-19）

## この作業環境で実行済み

- Linux / Python 3.13.5 / pytest 9.0.2。
- `python -m pytest -q`: **76 passed, 1 skipped**。
- 実FFmpegで合成映像＋音声を作成し、CPU変換 → ffprobeによる映像寸法・音声確認 → 元動画SHA-256不変を確認。
- 実TkをXvfb上で起動し、設定読み出し、言語切替、GPUタブ表示を確認。
- 実子プロセスによる起動・通常停止・強制停止・再起動・失敗終了の状態遷移を確認。
- 順序付き並列処理、先読み上限、producer待機時の保存継続、キャンセル、共通snapshot終端、設定検証／原子的保存、ログ上限、上書き拒否をテスト。
- `python -m livecatch_core diagnose`: FFmpeg/ffprobeあり。CUDAは `libcuda.so.1` をロードできず不使用。**GPU成功を確認した結果ではない。**

## 依存欠落により未実行

このコンテナは外部ネットワークに接続できず、yt-dlp Pythonパッケージをインストールできなかった。
`tests/test_ytdlp_integration.py` はモジュール単位でskip（内部に4テストケース）。
契約用fakeによるアダプターテストは通っているが、これを実yt-dlpの統合テスト通過とは数えない。
GitHub Actionsは固定依存をインストールして実HTTP統合テストも実行する設定。**CI設定を作ったこと自体は、CI通過を意味しない。**

## 合成loopback計測

`benchmarks/loopback.py` は1 MiB（32 fragment × 32 KiB）、各HTTP応答に10msの待機、8並列、3回の中央値を比較。
ライブ更新待ちケースでは9番目のfragmentを生成する前に160ms待つ。HTTPサーバーのaccept backlogは64。
比較相手は旧コードと同じeager `ThreadPoolExecutor.map` という**スケジューリング方式**であり、LiveCatch本体／実yt-dlp／YouTubeのベンチマークではない。
全出力のSHA-256一致をassertしている。生データは `benchmarks/results.json`。

| 条件 | eager 8並列 | bounded 8並列 |
|---|---:|---:|
| 有限backlog・総時間 | 50.9ms | 51.5ms |
| live待機あり・最初の保存 | 162.5ms | 11.1ms |
| live待機あり・総時間 | 203.4ms | 204.9ms |

**総スループット向上は示していない。** 主な改善はlive生成器の先読み待ちを保存側から切り離すことと、未保存fragmentを有限個に保つこと。
低遅延loopbackでの数字を、実配信で「十数倍速くダウンロードできる」とは解釈しない。

## mainへのマージ前に必要な実機確認

1. CIのPython 3.10/3.13 × Windows/Linuxで実yt-dlp統合テストを通す。
2. Windowsで通常起動、PyInstaller版GUI＋worker起動、Cookie利用、待機枠の開始、通常停止と結合完了を確認。
3. 許可されたYouTube DVRで音声・映像のsnapshot終端、同期、長時間DVR offset、終了間際、再接続、resumeを確認。
4. Twitchの現行ライブ通常録画と停止、VOD取得を確認。ライブsnapshot非対応を確認。
5. NVIDIA実機でCUDAプローブ、NVDEC対応コーデック、scale_cuda、NVENC、GPU失敗時CPU fallbackを確認。
6. 同一入力／画質条件でCPUとGPUの経過時間・画質・容量・VRAM・GPU同時数1/2/4を比較。速度倍率は測定後に記録。

残る制約: snapshotはYouTube内部sequenceメタデータ依存、初回観測時点で固定、異なるaudio/video開始位置とA/V同期は実配信未検証。
GPU exportは別ファイルへの非可逆H.264/AAC MP4でHDR非対応。NTFS等hard link対応保存先が必要。
再開checkpointの正しさは単体テストのみ。強制停止／停電／ディスク満杯を含む実ダウンロードresumeは未検証。
GUIで同時に録画するURLは1件。並列ファイル変換はCLIで複数ファイルを渡す。
