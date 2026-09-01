# 実機なし保守ラボ

このラボは、M5Stack をフラッシュせずに StackChan_EX_Amigo の再現・改修・検証・復旧を進めるための基盤です。通信の契約と server の判断を先に固定し、実機確認は hardware 固有部分を触ったときの最後の短い smoke test に絞ります。

## できること

| 層 | 実機なしで確認する内容 |
| --- | --- |
| 仮想 M5Stack | `/play`、`/play_local`、`/audio/status`、`/camera/trigger`、旧 `/speech`・`/chat`・`/face`、busy 状態、音声取得、カメラ callback |
| firmware 契約 client | STT multipart、chat JSON、TTS JSON、Vision JPEG/query の現行 payload |
| fake service | Whisper/OpenAI 互換 STT・chat・TTS、voice server、固定 WAV/MP3、指定回数だけの HTTP/timeout/空/壊れた JSON |
| Amigo server | app factory、主要 HTTP route、短すぎる音声、camera action、Vision queue full、runtime 非起動 |
| 設定診断 | Python/package、dotenv の重複 key、数値/URL/path、JSON、書込先、ffmpeg。値は診断結果へ出さない |
| 復旧 | 明示した設定・JSON・memory・archive の hash 付き ZIP、改ざん検知、空 directory への安全な restore |
| ローカル保存 | `today.md` や ConfigUI 書込を一時ファイル + fsync + atomic replace で更新し、直前版を `.bak` に保持 |

`python3 -m tools.stackchan_lab smoke --repo .` は loopback 上だけで、次を一周します。

1. firmware と同形の STT/chat/TTS request を fake service へ送る。
2. server が M5Stack へ送る `/play` を仮想端末で受け、音声を取得して hash を記録する。
3. 仮想端末を camera 状態へ進め、固定の合成 JPEG を callback する。
4. firmware source の route 名が契約から消えていないか静的確認する。

外向き API、Discord、実 M5Stack、VOICEVOX、Whisper、MLX/Kokoro、モデル download は使いません。

必須 check が落ちると exit `1`、必須 check は通っても既知差分が残る間は `status: degraded` と exit `2`、すべて解消すると exit `0` です。CIの既知差分固定だけは `--allow-known-gaps` を付け、必須 check の失敗と新しい差分は引き続き失敗させます。

## 最短の使い方

Python は `.python-version` の 3.11.14、直接依存は `uv.lock` で固定しています。

```bash
# 新しい仮想環境を初回作成する場合。core + test のみで、モデルは取得しない
uv sync --frozen --no-python-downloads --group test

# 以後の検証
uv run --frozen --no-sync pytest -q
uv run --frozen --no-sync python -m tools.stackchan_lab smoke --repo .
```

既に必要 package が入っている環境なら、環境を作り直さずに実行できます。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 -m pytest -p no:cacheprovider -q
python3 -m tools.stackchan_lab smoke --repo .
```

`smoke` と live HTTP test は `127.0.0.1` の一時 port を使います。制限付き sandbox では loopback bind の許可が必要な場合があります。

## 設定を壊す前の診断

`doctor` は read-only です。dotenv の key 名と値の形は検査しますが、値そのものを JSON 出力しません。ネットワーク接続もしません。

```bash
python3 -m tools.stackchan_lab doctor --repo . --env-dir ~/env
```

終了 code は、error があれば `1`、warning だけなら `0` です。warning は optional model/package や未設定 path を含みます。

test import 時に実運用 dotenv を読ませない場合は、`STACKCHAN_SKIP_DOTENV=1` を使います。pytest では常にこの境界を設定し、runtime service、scheduler、起動音声、モデル preload を開始しません。

## 仮想 M5Stack を単体で起動

```bash
python3 -m tools.stackchan_lab serve-device \
  --host 127.0.0.1 \
  --port 18080 \
  --server-url http://127.0.0.1:5050
```

制御 endpoint はローカル検証専用です。

- `GET /__lab__/state`: 現在状態と event 履歴
- `POST /__lab__/advance`: 再生/camera を一段進める
- `POST /__lab__/reset`: 状態と fault を初期化
- `POST /__lab__/fault`: 指定 path の次回応答へ fault を積む
- `POST /__lab__/camera-transport`: camera body 部分送信や、完送後の応答消失を積む

例えば `/play` を一度だけ 503 にします。

```bash
curl -X POST http://127.0.0.1:18080/__lab__/fault \
  -H 'Content-Type: application/json' \
  -d '{"path":"/play","kind":"http","status":503,"times":1}'
```

HTTP 504 と socket timeout は別物として扱います。`kind: http_status` は即時 status、`kind: socket_timeout` は遅延応答です。connection refused は、ラボが確保した「bind 済みだが listen していない」loopback port で再現します。

camera upload は `partial_send` と `response_lost_after_body` を別に注入できます。現 firmware と同じく、body 未完送だけ最大1回 retryし、body 完送後に response が消えた場合は synthetic 202 として成功・retryなしにします。

virtual device が音声 URL を取得するのは `http://localhost` または loopback IP だけです。ラボを SSRF の踏み台にしないため、外部 URL は記録だけして取得しません。

## Snapshot と restore

既定対象は、指定した source root 配下の `env`、`server/json`、`server/memory`、`server/archive` です。存在するものだけを取り込みます。

```bash
python3 -m tools.stackchan_lab snapshot create \
  --source /path/to/portable-root \
  --output /safe/off-repo/amigo.stackchan-snapshot.zip

python3 -m tools.stackchan_lab snapshot verify \
  /safe/off-repo/amigo.stackchan-snapshot.zip

python3 -m tools.stackchan_lab snapshot restore \
  /safe/off-repo/amigo.stackchan-snapshot.zip \
  --target /path/to/new-empty-directory
```

注意点:

- Snapshot には API key、会話、写真などの private data が入り得ます。Git へ commit せず、必要なら OS の暗号化領域に保存してください。
- archive 内には元の絶対 path を保存しません。
- symlink、`..`、絶対 path、manifest 外 payload、hash/size 不一致を拒否します。
- restore は既存運用 directory を上書きせず、新規または空 directory だけを対象にします。検証後に人間が差分を見て切り替えます。

## 現在固定している既知差分

現 firmware は camera trigger で `archive` / `transient` を内部利用しますが、`/vision/upload` query には mode を載せません。ラボは旧契約を再現し、server 側で mode が保持されるべき test を strict xfail にしています。

この修正には firmware 変更と、最終的に一度だけ実機確認が必要です。今回のラボ導入では firmware を変更せず、フラッシュも行いません。

## どこまで実機不要か

フラッシュ不要:

- server の route、prompt 前後処理、設定 validation、ログ/記憶、Vision queue、復旧処理
- firmware/server 間の HTTP payload と error handling の大半
- timeout、壊れた JSON、空 response、busy、queue full の再現
- HTTP error / socket timeout / connection refused の区別、camera 部分送信 retry、完送後 ACK 消失
- dependency lock、CI、静的な firmware route drift 検知

最後に実機が必要:

- display、servo、speaker/I2S、microphone、camera sensor、PSRAM、SD、Wi-Fi、発熱/電源
- PlatformIO board/library/toolchain 差
- firmware binary を変更した後の boot と一周 smoke

したがって server/テスト/保守ツールの改修では通常フラッシュ不要です。firmware 変更をまとめて十分に仮想検証し、実機への書込回数を減らします。

## 公開/非公開境界

この repository のラボは合成データだけで誰でも再現できる engine です。個人の hostname、LAN 構成、実メンバー、実会話、写真、音声、snapshot、secret は置きません。家庭固有の横断設定と運用 wrapper は private maintenance repository の隔離 directory で管理し、実データ自体はそちらでも ignore します。

Python package と追加 tool のライセンス確認は [python-dependency-licenses.md](python-dependency-licenses.md) にまとめています。
