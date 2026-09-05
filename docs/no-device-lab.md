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
| 安全な追跡 | HTTP route、status、時間、byte数だけをローテーション付きJSONLへ保存。本文・query・header値・URL・例外文は保存しない |
| 問合せ資料 | allowlistで再検査した追跡情報と環境診断だけを固定構成ZIPにし、hashで改ざんを検出 |
| 処理台帳 | Vision、音声push、日次要約、LINE通知をappend-only journalとprivate spoolへ受付後に応答し、再起動時の状態を判定 |
| LINE Queue | AES-GCM暗号文、固定Cloudflare URL、認証header、永続化後ACK、retry、redelivery重複排除、60秒schedulerを合成fixtureで確認 |
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

## 秘密を含めない trace と support bundle

通常のHTTP requestには `X-Trace-ID` が付き、既定では `~/.local/state/stackchan-ex-amigo/trace` に小さなJSONLを保存します。記録対象は固定したcomponent/operation/status、所要時間、request/response byte数などの数値だけです。request/response本文、query、header値、IP、prompt、会話、画像、音声、URL、設定値、例外messageには保存先のfield自体がありません。

共有用資料は repository を再帰的に固めず、固定した6項目だけを新規ZIPへ書きます。

```bash
python3 -m tools.stackchan_lab support create \
  --repo . \
  --env-dir ~/env \
  --output /safe/off-repo/amigo.stackchan-support.zip

python3 -m tools.stackchan_lab support verify \
  /safe/off-repo/amigo.stackchan-support.zip
```

bundleにはdotenv値、prompt/transcript、画像、音声、既存log、memory/archive、hostname/username、絶対path、Git remote/diffを入れません。traceも取り込む際にallowlistから再構築し、不明fieldや壊れた行を落とします。それでも共有前には必ず内容を確認してください。出力先はrepository外かつ未作成のpathだけを許可します。

## Crash後に再開できる処理台帳

既定の `~/.local/state/stackchan-ex-amigo/journal` に、内容を持たないappend-only JSONLとprivate spoolを分離して保存します。journalにはoperation ID、処理種別、状態、試行回数、payloadの個数・総byte数しか入りません。画像、transcript、音声URL、日次会話はmode 0600のspoolに置き、hash検証後だけ読みます。

- VisionはJPEGと文脈をfsyncしてから `processing` を返し、成功時だけspoolを消します。
- 非同期音声pushは受付を永続化してから返します。外部POST前は `dispatching`、応答確認後は `acked` です。
- crash時の `running` は `retry_wait` へ戻します。POST済みか不明な `dispatching` は二重再生を避けるため `uncertain` とし、自動再送しません。
- 日次要約はatomic archive、`today.md` 更新、archive hash付きcomplete markerの順で完了します。部分archiveの存在だけでは処理済みにしません。
- 任意のCloudflare Queue経路では、LINE通知の暗号文を復号し、ローカルmember照合とprivate spoolへのfsync後だけACKします。同じ`webhookEventId`のredeliveryは決定的operation IDで重複排除します。通常利用のRender経路には、このQueue固有の保証は適用されません。
- JSONLの末尾だけが途中で切れた場合は最後の未完行を無視し、中間破損はfail-closedでreadinessを落とします。

## 統一config検査

```bash
python3 -m tools.stackchan_lab config validate --repo . --env-dir ~/env
python3 -m tools.stackchan_lab config plan --json-dir server/json
```

`validate` はenv key契約、announcements/member/reading map/voice catalog、SDテンプレートを同じ診断形式で検査し、値を出力しません。`plan` は旧announcement曜日flagと `children`/`master` driftの件数をread-onlyで返し、ファイルを変更しません。サーボlimit、人物割当、実LAN URLは機体・家庭ごとの判断が必要なので自動migration対象外です。

serviceのliveness/readinessと起動wrapperは [local-service-operations.md](local-service-operations.md) を参照してください。

## 仮想 M5Stack を単体で起動

```bash
python3 -m tools.stackchan_lab serve-device \
  --host 127.0.0.1 \
  --port 18080 \
  --server-url http://127.0.0.1:5050
```

制御 endpoint はローカル検証専用です。

仮想端末は既定で撮影modeを送る改修版を再現します。未Flashの旧版を再現する場合は
`serve-device` に `--legacy-camera-upload` を追加してください。既存の
`--camera-upload-v2` も互換用に受け付けます。

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

撮影modeの欠落は本改修のソースで修正しました。`/vision/upload` の任意query `mode` を
`archive` / `transient` として送り、serverの受付→private spool→handlerへ引き継ぎます。
対応テストはstrict xfailではなく通常の成功が必須で、smokeでもmode保持を検査します。
ラボの契約版は1.1です。これはHTTPで送るprotocol IDではありません。

- 明示modeが発話中のキーワードより優先します。空文字・未知値は `transient`。
- 完全にmodeを省略した旧firmwareと、mode fieldのない旧spoolは、従来のキーワード判断を維持します。
- `transient` は資料アーカイブを作らない意味です。明示的なカメラ会話の通常履歴や処理中の一時ファイルを禁止する意味ではありません。一時画像は従来どおり成功・失敗後に削除します。
- この変更は未Flashです。シミュレータの成功は実機のカメラや通信を検証したことにはなりません。

## どこまで実機不要か

フラッシュ不要:

- server の route、prompt 前後処理、設定 validation、ログ/記憶、Vision queue、復旧処理
- firmware/server 間の HTTP payload と error handling の大半
- timeout、壊れた JSON、空 response、busy、queue full の再現
- LINE Queueの暗号化payload、認証付きpull、ACK/retry、redeliveryとローカル再開
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
LINEの60秒pull経路、公開/非公開境界、切替と切戻しは [line-queue-relay.md](line-queue-relay.md) を参照してください。
