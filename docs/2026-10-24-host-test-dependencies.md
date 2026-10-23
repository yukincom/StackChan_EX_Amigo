# 本体ライブラリを使うホスト試験（2026-10-24）

## 依存と対象

前回の2 skipsは、検証用worktreeに本体のライブラリがなかったためです。本体で不要な依存という意味ではありません。

| 依存 | 版 / upstream commit | 検証する内容 |
| --- | --- | --- |
| M5GFX | 0.2.26 / `729297d6e3d657ddc1ec5189bac2f2ea68828085` | 実enumによる入力メニューの中央配置 |
| ESP8266Audio | 1.9.9 / `a0a4a007576e0a26a51f74d2d9fc2d698b3f9ba8` | 実MP3デコーダーとproduction再生完了処理 |

既存のCoreS3ビルド環境で使用している版と同じです。ローカルでは既存の `.pio/libdeps` を検証用worktreeから読み取り再利用し、新たな本体build・Flash・再起動は行いません。`cc` / `c++` / `ffmpeg` も既存のものを使います。ライブラリ、音声モデル、個人データをGitへ追加しません。

## CI

既存workflow内で、上表の公式repositoryのcommitを `actions/checkout` で取得します。Ubuntu公式のapt経由でffmpegを用意し、compilerと必要ファイルの存在を確認してから全体試験を実行します。依存取得の失敗をskipとして扱いません。

CIのトリガーは従来どおりpushのみです。定期・PR・手動実行のトリガーは追加しません。firmwareやserverの実装、Python依存、Cargo依存は変更しません。

## 検証

```bash
python -m pytest -p no:cacheprovider -q -rs \
  tests/unit/test_firmware_touch_input_menu.py \
  tests/unit/test_firmware_mp3_playback.py
```

既存のテスト環境で実行し、入力操作の既存試験を含めて **3 passed、skipなし**。実M5GFXヘッダー、ESP8266Audioのlibmad/MP3実装、合成MP3と既存SD用音声を使います。画面表示・スピーカー・実機での動作確認とは区別します。

macOS arm64の全体再検証は **353 passed、skipなし**（27.05秒）。CIのYAML / Bash構文とpushのみのトリガーも検査しました。再利用したM5GFX enum、MP3 decoder、libmad configのGit blob hashは、CIで取得する公式commitのファイルと一致しています。GitHubのLinux実行結果はpush後に別途確認します。
