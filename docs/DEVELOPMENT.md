# 開発者向けガイド

## ソースから起動する

Python 3.10以上を使います。通常の録画にはGPUは不要です。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python livecatch.py
```

FFmpeg・ffprobe・Denoは `tools/` またはPATHへ配置します。Windows配布版には同梱します。yt-dlpは `2026.8.19` 固定で、内部アダプターは未確認バージョンへの適用を拒否します。単に依存のピンだけを更新しないでください。

端末の文字コードが合わない場合は、起動前に `chcp 65001` と `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` を設定します。これはすでに壊れたGUI文字列の万能修復ではありません。

## 責務の分離

- `worker.py`: yt-dlp呼び出し・設定・録画対象の検証・イベント変換。
- `ytdlp_patch.py`: 固定版の内部APIへの最小限のアダプター。取得・復号・順序付き追記・チェックポイントの基本動作は上流を利用。
- `fragment_telemetry.py`: thread-safeな取得完了数・追記数・累積バイト数・YouTube sequenceの観測。動画データそのものは保持しない。
- `progress.py`: 手動／自動録画で共用する純粋な進捗更新・集計・表示文言。Tk・ネットワーク・ファイルI/Oに依存しない。
- `events.py`: IPCの間引き・最新値への集約。ログの大量発生で進捗や終了通知が消えないよう別管理。
- `gui.py` / `twitch_watch_gui.py`: 画面だけを担当。GUIは100ms単位のイベント処理でまとめて描画。
- `twitch_watch.py`: チャンネルの検知・録画枠・再試行・停止のスケジューラー。録画進捗の計算は共通モデルへ委譲。

進捗には単発の「今回のbytes」ではなく累積カウンターを使います。イベントが間引かれても合計が変わらないためです。配信に割り当てるIDはネットワークと追記で共通にし、旧 `0:137` / `137` の二重計上を吸収します。

取得完了と追記完了は別です。遅い先頭断片の前へ後続を挿入すると動画や再開位置が壊れるため、順序保証を維持します。先読みウィンドウの完了を待つバッチ保存、メディアの無制限RAM蓄積、毎書き込みの追加fsyncは行いません。

YouTubeの `fragment_count` は排他的なsequence終端です。追いつき率の分母を `head - first_sequence`、分子を `committed_sequence - first_sequence + 1` とします。先読み時点の古い終端ではなく、追記時点の最新観測終端を使います。15秒以上古い終端を新たなLIVE判定には使いません。計測不能なTwitch/外部FFmpeg経路にYouTube用の％を流用しません。

## テスト

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

LinuxのGUIテストは `xvfb-run -a python -m pytest -q` と `xvfb-run -a python scripts/gui_smoke.py`。実yt-dlp＋ローカルHTTPテストには、先頭断片だけを遅延させ、後続の取得表示が保存待ち中にも届くこと・最終バイト列が順序どおりであることの検証があります。外部サイト・Cookieは使いません。

再現用の測定は `python benchmarks/telemetry_latency.py`。これはイベント配送遅延の合成比較で、実インターネット速度やSSDスループットの測定ではありません。

## Windows配布版

Inno Setup 6を用意し、`build_release.bat <__version__と同じ番号>` を実行します。本体・Worker・Updaterを同じソースからビルドしてください。

リリース時は `livecatch_core/__init__.py`、CHANGELOG、インストーラーのバージョンをそろえます。CI成功を確認してから `.github/release-version.txt` を更新すると、mainのReleaseワークフローでSetup.exeとUpdate.zipを生成・公開します。ソース反映と更新パッケージ公開は別段階です。Windowsビルドには完成exeの日本語Tkスモークテストを含みます。

通常録画からGPU/CPU追加変換は呼びません。明示的な変換が必要な開発者向けに `python -m livecatch_core export ...` は残っています。

実サイトでの長時間録画、個々のSSD・セキュリティソフト・回線環境における速度は、合成テストの成功だけでは保証できません。
