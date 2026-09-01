# LINE Queue relay

LINE通知は、公開Webhookと自宅Macを直接つながず、Cloudflare Queueを間に置くpull方式を使えます。Mac側は従来どおり60秒間隔で取りに行くため、自宅routerのport開放、Tunnel、M5Stack firmwareの変更やフラッシュは不要です。

```text
LINE -> 署名検証Worker -> 暗号化 -> Cloudflare Queue
                                      |
                                      | 60秒ごとの認証付きpull
                                      v
Mac server -> private spool -> 音声生成 -> M5Stack
```

## 安全境界

- WorkerはLINEのraw bodyを変更せずに署名検証し、許可したuser IDのtext eventだけを受理します。
- Queue上のuser ID、本文、timestampはAES-256-GCM暗号文です。復号鍵はWorker secretとMac側の環境変数にだけ置きます。
- Macは固定形式のCloudflare API URLだけを許可し、API tokenをAuthorization header以外へ出しません。
- Queue messageは、復号、ローカルuser照合、private spoolへのfsyncが完了してからACKします。
- LINEの`webhookEventId`から内容を含まないoperation IDを作り、redeliveryや再起動で同じ通知を二重登録しません。
- journalとtraceには本文、user ID、token、鍵、URLを保存しません。本文はmode 0600のprivate spoolにだけ置き、完了後に削除します。
- 旧Render経路は移行中のfallbackです。Queue設定3項目がそろうとQueueを優先します。

## Mac側の設定

`~/env/.env.local`またはConfigUIへ、次の3項目を設定してserverを再起動します。実値はrepositoryへ入れません。

```dotenv
LINE_QUEUE_PULL_URL=https://api.cloudflare.com/client/v4/accounts/ACCOUNT_ID/queues/QUEUE_ID/messages/pull
LINE_QUEUE_API_TOKEN=<set-locally>
LINE_RELAY_KEY=<set-locally>
```

Pull URLはCloudflareの`api.cloudflare.com`、32桁のaccount IDとqueue ID、`/messages/pull`の完全一致だけを受け入れます。tokenは使用するaccountに限定し、QueuesのRead/Write（pullとACKに必要）だけを付与します。

設定が不完全または不正ならQueue pollは起動しません。`RENDER_URL`が残っていれば旧経路を使い、両方がなければLINE通知だけを無効にして会話機能は起動します。

## 実機なし検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 -m pytest -p no:cacheprovider -q tests/unit/test_line_queue_service.py

PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 -m pytest -p no:cacheprovider -q tests/unit/test_line_scheduler.py
```

合成した署名済み暗号文だけを使い、復号改変検知、固定URL、token非露出、永続化後ACK、未登録userのretry、poison messageの隔離、redelivery重複排除、60秒間隔を確認します。LINE、Cloudflare、M5Stackには接続しません。

## 切替と復旧

公開Webhook Worker、秘密値、Queue作成、LINE Developers ConsoleのWebhook URLは家庭固有なので、非公開の関連maintenance repositoryで管理します。切替は次の順に行います。

1. 公開側Python testと非公開Worker test/dry-runを通す。
2. WorkerとQueueを作成し、専用tokenと共通暗号鍵を設定する。
3. Mac側の3項目を設定して再起動し、Queue poll開始を確認する。
4. LINE Webhook URLをWorkerへ切り替え、合成でない通知を1件だけ確認する。
5. 問題がなければ`RENDER_URL`を空にする。

問題があればLINE Webhook URLを旧Renderへ戻し、Mac側の`LINE_QUEUE_*`を一時的に空にすれば旧経路へ戻せます。暗号鍵を変更すると古いQueue messageは復号できないため、通常はQueueが空であることを確認してからWorkerとMacを同時に更新します。

## 依存とライセンス

Mac側で追加する`cryptography`は`Apache-2.0 OR BSD-3-Clause`です。Worker本体はWeb Cryptoだけを使います。配備用Wranglerは非公開maintenance repositoryで開発依存として固定し、実行時依存にはしません。Python依存の一覧は[python-dependency-licenses.md](python-dependency-licenses.md)を参照してください。
