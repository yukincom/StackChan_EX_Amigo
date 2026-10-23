# eMudhra / emSign CA対応（2026-10-23）

## 理由と影響

[Python公式の告知](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)に従い、侵害された中間CAに関係するルートを含む旧bundleの使用を終了します。
pipはOS側の信頼設定に加えて同梱certifiも使用するため、OSのCA更新だけでは旧pipの問題を解消できません。

必須最低版は certifi **2026.10.19**、uv **0.13.4**、pipを導入する環境では **26.2.2** です。
certifiを直接依存にし、uvの `required-version` を設定しました。app / voice server / model downloadの起動と設定読込時に、古い・欠落したcertifi、導入済みの古いpip/uvを拒否します。
pip/uvを含まない実行用venvに追加導入を強制するものではありません。

既存環境を黙って継続する後方互換は廃止しました。検査を無効にする環境変数、古いuvへのPATHフォールバック、TLS検証無効化は用意していません。
`stackchanctl run-server` は指定uvの版を確認し、既定では専用 `.bootstrap-venv` のuvを使用します。offline `doctor` は拒否理由を診断結果へ追加します。

## 移行と再ビルド

既存のサービスはこのソース変更では再起動しません。環境を更新しても、既に動いているPython processは旧bundleを保持し得るため、運用切替時には再起動が必要です。

```bash
python3.11 tools/bootstrap.py
.bootstrap-venv/bin/uv lock --check --no-python-downloads
.bootstrap-venv/bin/uv sync --frozen --no-python-downloads --group test
.bootstrap-venv/bin/uv run --frozen --no-sync python -c 'from server.security_runtime import require_secure_environment; require_secure_environment()'
.bootstrap-venv/bin/uv run --frozen --no-sync pytest -q
.bootstrap-venv/bin/uv run --frozen --no-sync python -m tools.stackchan_lab smoke --repo . --allow-known-gaps
```

Windowsのuvは `.bootstrap-venv/Scripts/uv.exe` です。optional `voice` / `mlx` 利用時は同じ安全なuvで必要な `--extra` を付けて同期してください。モデルの自動取得はしません。
既存venvにpipを残す必要がある場合も、公式告知のSHA-256付きpip wheel URLで26.2.2へ更新してから使用します。不要なら旧venvを別名で保管し、新しい `.venv` を作成してください。

bootstrapは専用venvを作成し、最初のnetwork installを公式pip 26.2.2 wheelの固定URLとSHA-256で検証します。依存解決なしでそのwheelを導入してから、安全版pipでuv 0.13.4を導入します。グローバルPythonのpackageは変更しません。
コンテナや配布環境はOSのCAも更新し、キャッシュなしで再作成してください。ホストOS全体の信頼storeはこのrepositoryから変更しません。

## firmwareと実機の境界

`firmware/src/rootCA/` の4 PEMはGTS Root R1/R4とISRG Root X1で、今回のemSign削除対象ではありません。firmwareのCA・保護設定・binaryは変更しません。
読み取りで別課題として `WebVoiceVoxRootCA.h` のISRG Root X1クロス署名証明書は2024-09-30で期限切れと確認しました。今回のCA侵害とは別で、使用経路の確認と更新・実機検証が必要です。今回このheaderや実機は変更していません。
CoreS3本体単体の連続稼働試験、既存collector、常駐serverは維持します。Flash・本体再起動・実機smokeは実施しません。

## 検証

2026-10-23、公開main `f177983` からの専用worktreeで実行しました。

- 公式hash付きbootstrap成功: pip 26.2.2 / uv 0.13.4。
- certifiだけを2026.7.22から2026.10.19へ更新。他packageの固定版は維持。uv 0.13.4によりlock revisionとmarkerの表現も更新。
- `uv lock --check --offline --no-python-downloads`: 成功。旧uv 0.10.2の同commandは必須版不一致で拒否。
- 安全版判定・旧版拒否・bootstrap最初の通信・既存wrapper/doctorの対象テスト: 23 passed。
- no-device全体: **316 passed, 2 skipped**（13.50秒）。skipは任意のM5GFX header / ESP8266Audio依存を必要とする2件。ArduinoJson 7.4.3は既存cacheを読み取り再利用。
- standalone smoke: **status ok、9 checks成功、known_gaps空、route差分なし**。
- 実機、音声/カメラhardware、MLX/Kokoro推論、Windows実行、コンテナ再ビルド、常駐processへの適用は未検証です。
