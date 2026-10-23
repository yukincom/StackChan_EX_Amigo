# ローカルserver運用

この手順はfirmwareを書き換えません。`stackchanctl` はrepositoryと実行環境のidentityを確認し、loopbackの状態確認または既存server processの起動だけを担当します。launchdへの登録・解除は自動実行しません。

## 手動確認

初回だけ、既存のRust toolchainでCLIをビルドしてください。Pythonアプリの通常起動にはRustは不要です。

```bash
cargo build --release --locked --manifest-path native/stackchanctl/Cargo.toml
native/stackchanctl/target/release/stackchanctl preflight --repo . --env-dir ~/env
native/stackchanctl/target/release/stackchanctl run-server --repo . --env-dir ~/env --dry-run
native/stackchanctl/target/release/stackchanctl status --url http://127.0.0.1:5050
```

Windowsでは末尾に `.exe` を付けます。互換入口 `python3 tools/stackchanctl.py ...` も同じRust binaryへexecします。未ビルド時にPython実装へ戻ったり自動取得したりはしません。別のbuild先は `STACKCHANCTL_BIN` で指定できます。
`preflight` だけは既存のoffline Python doctorを利用します。Pythonは `STACKCHAN_PYTHON`、repositoryの `.venv`、PATHの `python3` / `python` の順で選択します。

- `/health/live`: Flask processが応答できるか。runtime準備完了を意味しません。
- `/health/ready`: 設定、operation journal、Vision worker、音声送達worker、runtime起動がすべて正常なときだけ200です。
- 旧 `/health` は既存client互換のため残しています。

`status` はlocalhostまたはloopback IPの平文HTTPだけを許可し、proxyやredirectを使いません。任意URL取得には使えません。`run-server` は `--uv`、`.bootstrap-venv`、PATHの順でuvを選び、最低版0.13.4を検査してから `uv run --frozen --no-sync` を使います。CLIによる依存同期はしません（アプリ側のモデル等の挙動は従来どおりです）。初回は [アップデート手順](2026-10-23-emsign-security.md) に従ってください。実装範囲と検証記録は [Rust移行記録](2026-10-23-stackchanctl-rust.md) を参照してください。

## launchdテンプレート

[`ops/launchd/com.stackchan.amigo.server.plist.example`](../ops/launchd/com.stackchan.amigo.server.plist.example) は公開可能なplaceholderだけです。次をprivateなコピーで実在する絶対pathへ置換してください。

- `__STACKCHANCTL__`: ビルド済みRust CLIの絶対path
- `__UV__`: uv executable
- `__REPO__`: このrepository
- `__ENV_DIR__`: dotenv directory
- `__STATE_DIR__`: Git管理外のlog directory

置換後は `plutil -lint` と `native/stackchanctl/target/release/stackchanctl run-server --repo . --env-dir ~/env --dry-run` で検査できます。後者は前半と同じRust CLIで、`--repo` / `--env-dir` はテンプレートに設定した実在するpathへ置き換えてください。このrepositoryのtoolは `launchctl bootstrap`、`bootout`、service登録、service起動を行いません。stdout/stderrには既存serverの会話系logが含まれ得るため、公開support bundleには取り込まずprivateに保管してください。
