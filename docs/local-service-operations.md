# ローカルserver運用

この手順はfirmwareを書き換えません。`stackchanctl` はrepositoryと実行環境のidentityを確認し、loopbackの状態確認または既存server processの起動だけを担当します。launchdへの登録・解除は自動実行しません。

## 手動確認

```bash
python3 tools/stackchanctl.py preflight --repo . --env-dir ~/env
python3 tools/stackchanctl.py run-server --repo . --env-dir ~/env --dry-run
python3 tools/stackchanctl.py status --url http://127.0.0.1:5050
```

- `/health/live`: Flask processが応答できるか。runtime準備完了を意味しません。
- `/health/ready`: 設定、operation journal、Vision worker、音声送達worker、runtime起動がすべて正常なときだけ200です。
- 旧 `/health` は既存client互換のため残しています。

`status` はlocalhostまたはloopback IPの平文HTTPだけを許可し、任意URL取得には使えません。`run-server` は `uv run --frozen --no-sync` を使うため、起動時にdependencyやmodelをdownloadしません。

## launchdテンプレート

[`ops/launchd/com.stackchan.amigo.server.plist.example`](../ops/launchd/com.stackchan.amigo.server.plist.example) は公開可能なplaceholderだけです。次をprivateなコピーで実在する絶対pathへ置換してください。

- `__PYTHON_311__`: Python 3.11 executable
- `__UV__`: uv executable
- `__REPO__`: このrepository
- `__ENV_DIR__`: dotenv directory
- `__STATE_DIR__`: Git管理外のlog directory

置換後は `plutil -lint` と `stackchanctl.py ... --dry-run` で検査できます。このrepositoryのtoolは `launchctl bootstrap`、`bootout`、service登録、service起動を行いません。stdout/stderrには既存serverの会話系logが含まれ得るため、公開support bundleには取り込まずprivateに保管してください。
