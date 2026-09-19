# yt-dlp内部調査・変更記録

対象日: 2026-09-19。LiveCatchベース: `d2d5bddcf1215dbd08f1ba07af9db218926b66d5`。
上流固定版: PyPI `2026.8.19` / `yt_dlp.version.__version__ == 2026.08.19`。
上流commit: `594bd50c2c78ac432f81600d309fdc4e0a92d82c`。

## 実際に読んだコード

- https://github.com/yt-dlp/yt-dlp/blob/594bd50c2c78ac432f81600d309fdc4e0a92d82c/yt_dlp/downloader/fragment.py
- https://github.com/yt-dlp/yt-dlp/blob/594bd50c2c78ac432f81600d309fdc4e0a92d82c/yt_dlp/downloader/dash.py
- https://github.com/yt-dlp/yt-dlp/blob/594bd50c2c78ac432f81600d309fdc4e0a92d82c/yt_dlp/extractor/youtube/_video.py
- https://github.com/yt-dlp/yt-dlp/blob/594bd50c2c78ac432f81600d309fdc4e0a92d82c/yt_dlp/downloader/external.py

YouTube extractorは固定版blob `290c3e4bde1eeab4455fd5eae44f755d27281385` の1940〜2110行を確認。
`http_dash_segments_generator`、`sq`、exclusiveな`fragment_count`の生成を確認した。実依存の統合テスト・実配信確認は別途マージ条件とする。
FragmentFDはmasterと固定版のblobが共に `7852ae90d0c3cd459892ec7685f19ed9a872c1f1`。

## ボトルネックと変更箇所

`FragmentFD.download_and_append_fragments` の並列分岐は `pool.map(_download_fragment, fragments)`。
Python 3.10〜3.13のThreadPoolExecutor.mapは入力を先に消費するため、終端のない／待機するlive生成器に対して保存側が待たされる。
単に `-N` を128へ増やす対処では解決しない。Python 3.14にbuffersize引数があっても上流呼び出しは指定していない。

本実装は「生成器を進めるproducer」「HTTP worker群」「順序どおり保存するconsumer」を分離する。
semaphoreはconsumerが保存後に次の結果へ進むまで返却しない。未保存の完成fragmentも先読み上限に含める。
上流の映像・音声コーディネーターが入るThreadPoolを再利用せず、内側のpoolを独立させる。
未知の上流バージョン／シグネチャでは適用を拒否する。これは公式ダウンローダープラグインではなく、録画子プロセス内の限定的な内部メソッド置換。

上流HTTP進捗hookもctx.fragment_indexを書き換える。その値をresume checkpointにそのまま使わず、順序付きで保存している `-FragN` ファイル名からcommit番号を確定して、コピーしたctxでcheckpointを書き込む。

YouTube snapshotは `sq` の絶対番号と `fragment_count` のexclusive終端を利用する。
映像・音声の最初のheadをbarrierで揃え、小さい方を共通終端にする。DVR開始位置が0でない場合も、ローカルfrag_indexではなくsqを使う。
表示ログのregex、15秒待機、更新が遅い音声の除外、SIGINTによる擬似正常終了を廃止する。
ただし同一sequence番号が同一時刻を指すというサイト側の保証を独立検証していない。実配信でA/V同期の検証が必要。

外部FFmpegには、停止時に標準入力へ `q` を送り、特にconsoleのないWindows GUIでも通常終了を要求する。
この置換も既知版の `downloader.external.Popen` に限定する。結合用postprocessorのプロセスには適用しない。

## 保持した範囲と変更しない範囲

HTTP/署名URL/認証Cookie/復号/HTTP Range/resume/リトライ/コンテナ結合はyt-dlpの実装を利用する。
認証回避、サーバー側レート制限の回避、存在しないDVRの復元、未来の配信の先取りは行わない。
インストール済みyt-dlpのファイルや上流GitHubには書き込まない。独立した上流forkも作成していない。

## NVIDIA経路

一次資料: https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/ffmpeg-with-nvidia-gpu/index.html

通常録画は圧縮済みstream copyなのでCUDAの仕事を無理に増やさない。
任意の追加変換だけ `-hwaccel cuda -hwaccel_output_format cuda` → `scale_cuda` → `h264_nvenc` を使う。
NVDEC/NVENCは専用ブロック、CUDAカーネルはFFmpegの既存filterを利用する。自作CUDAカーネルは追加していない。
プローブはffmpeg -encodersの一覧確認ではなく、実フレームのCUDA upload/scale/NVENC実行。
実GPUの速度・互換性・セッション上限は本環境で測定できていない。
