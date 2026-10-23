# stackchanctlのRust移行（2026-10-23）

## 範囲と選択

既存 `tools/stackchanctl.py` のstatus HTTP取得、loopback URL検査、uv版確認、起動引数・環境・exec処理を `native/stackchanctl` へ置換しました。意味の重なる新しい運用経路は追加していません。Python入口は引数を渡してRust binaryをexecするだけで、Python実装へのfallbackや自動build/downloadはありません。

`preflight` の設定・package診断は既存のoffline Python doctorを呼びます。この部分、Flask、各SDK、MLX/Kokoro/WhisperのruntimeはPythonのままです。アプリ全体のRust化を意味しません。通常の `server/app.py` / `server/voice_server.py` の起動にRustは不要です。

Pytra本体はユーザー指定の `/Applications/Pytra` を読み取り利用し、本体buildは中央側に任せました。評価時HEADは `9f341e04fefd8eacac1081c59e80f4042ee80a6f` です。独自cloneやPytra改変は行っていません。Python 3.14で入口置換前の `9e5f5b2` の既存CLIを評価しました（出力先と作業directoryは専用一時領域）。

```bash
python3.14 /Applications/Pytra/src/pytra-cli.py -build /path/to/StackChan_EX_Amigo/tools/stackchanctl.py --target rs --rs-package --opt-level 0 -o /path/to/private-evaluation-output
```

結果は `error: build failed: unexpected token in expression: * at pos 106` でした。変換結果を採用せず、この小さなCLI境界だけを手書きRustにしました。Pytraの生成物/runtime/sourceは配布物に含みません。本体の修理・再buildはこの作業では行いません。

## レビューした意味と境界

- Rust全ソース、Cargo manifest/lock、Python互換入口、既存Python版との差分をレビューしました。旧版は `tests/fixtures/stackchanctl_python_reference.py` にテスト専用で固定し、実行時fallbackとしては参照しません。参照CLIは比較テストが子processに `STACKCHANCTL_TEST_REFERENCE=1` を明示したときだけ実行します。通常の直接実行はHTTP取得・uv版確認・server起動の前に終了code 2で拒否します（Pythonの `-O` 時も同じ）。これは誤用防止であり、セキュリティ境界ではありません。検証は `python -m pytest tests/unit/test_stackchanctl.py` から行い、運用には [ローカルserver運用](local-service-operations.md) のRust CLIを使ってください。
- HTTPは従来どおり平文loopbackのみ。statusごとの3秒deadline、64 KiB上限、object JSON、ready / not_ready / unreachableの終了code 0 / 2 / 1を維持します。APIのUTF-8 JSONが対象です。
- 任意精度JSON整数を有効にし、日本語・emojiと `2**100` が旧Python版と一致します。uv版は固定幅整数へ変換せず桁数と数字列で比較します。
- `http::Authority::port()` が不正portでもNoneを返す点を補正し、65536や非数値を既定80へ誤変換しないようにしました。
- 意図した境界変更として、proxy環境変数とHTTP redirectを使いません。外部への転送を防ぎます。空query/fragmentも拒否し、非標準JSONのNaN/InfinityやUTF-16入力はサポートしません。
- uvの選択は明示 `--uv` → repositoryのbootstrap uv → PATHです。既存の更新済みPATH uvも使用できます。版確認はstdoutを並行drainし、保持は先頭4 KiBのみ。子が終了しても子孫がstdoutを保持する場合を含めて10秒deadlineを適用します。timeout時は失敗してCLI終了し、ブロックしたreaderをjoinしません。
- shellで起動引数を組み立てず、Unixではexecで置換し、Windowsでは終了codeを伝播します。日本語・空白path、cwd、`STACKCHAN_ENV_DIR`、stdin/stdout/stderr、終了codeをfake uvで比較しました。実serverの起動は行っていません。
- unsafeを自前crateで禁止しています。HTTPは `ureq` のdefault featuresを無効にし、TLS client / CA bundleを組み込んでいません。CA更新を検査するPython runtimeは引き続き別境界です。

## ビルド・ローカル検証

既存Rust 1.85以上と更新済みPython環境を使用します。検証機はmacOS arm64、既存Rust 1.98.1、Python 3.11.14でした。新しいtoolchainを導入したり共有toolchainを変更したりはしていません。

```bash
cargo fmt --manifest-path native/stackchanctl/Cargo.toml -- --check
cargo test --locked --manifest-path native/stackchanctl/Cargo.toml
cargo clippy --locked --manifest-path native/stackchanctl/Cargo.toml --all-targets -- -D warnings
cargo build --release --locked --manifest-path native/stackchanctl/Cargo.toml
.venv/bin/python -m pytest -q
```

依存がcache済みなら各Cargo commandへ `--offline` を付けられます。初回crate取得のCAは更新済みOS store、または `CARGO_HTTP_CAINFO` に更新済みcertifiのPEMを指定します。TLS検証を無効にする手順はありません。ユーザー手順は [ローカルserver運用](local-service-operations.md) を参照してください。

今回のローカル結果:

- Rust unit 5件成功（1 MiB出力、子孫のstdout保持deadlineを含む）、release build・Clippy警告なし。
- Python no-device全体 **344 passed, 2 skipped**（25.34秒）。native binaryをbuildした状態で、旧版比較と正常/異常preflight・status・run-serverを実行。skipは従来の任意M5GFX header / ESP8266Audio依存2件のみです。
- Python compileall、launchd templateの `plutil -lint` 成功。
- standalone smokeの9 checks成功、known_gaps・firmware route差分なし。
- 実numpy / soundfile / kokoroのimportと実voice serverの一時HTTP起動、日本語生成→WAV配信の5 checks成功。上流VOICEVOXは固定WAV fixture、Kokoroモデルは未読込です。
- 同じloopback health fixtureへ別processで各50回アクセスしたmedian: 旧Python CLI **276.127 ms**、Rust CLI **6.834 ms**。macOS release binaryは **1,992,400 bytes**。CLI起動とローカルHTTPの測定であり、LLM・音声推論の高速化を示す値ではありません。

GitHub Actionsは一時停止指示の後、ユーザーの再開指示により最終push時に有効化・実行確認する運用です。実行トリガーはmainへのpushのみで、定期実行・PR作成時・手動実行トリガーはありません。既存のno-device workflowにRust test/buildを含め、比較テストが未buildのためskipされないようにしました。

Windowsの実行、実サーバーの運用切替、モデル推論、hardwareは未検証です。CoreS3のFlash・restart、collectorや常駐serviceの変更・再起動は行っていません。バイナリReleaseは作成せず、今回の公開対象はソースとlockfileです。

### レビュー追補: テスト参照CLIの誤用防止

無許可のHTTP取得拒否を環境変数未設定・空文字・`0` と通常 / `-O` の6通り、uv版確認・server起動前の拒否を1通り追加しました。明示許可した従来の新旧比較も継続し、CLI試験 **40 passed**、no-device全体 **351 passed, 2 skipped**。skipは従来の任意M5GFX / ESP8266Audio依存2件です。運用ドキュメントのBash構文検査も成功しました。Rust実装・Python互換入口・CIトリガーは変更していません。

## 依存ライセンス

Cargo.lockを固定し、cache済みmacOS対象のCargo metadataでライセンスを確認しました。直接依存は clap 4.6.7、http 1.5.0、serde_json 1.0.154、ureq 3.5.0、wait-timeout 0.2.1で、いずれもMIT / Apache-2.0の選択式です。その他にMIT、Unlicense OR MIT、およびunicode-ident 1.0.27の `(MIT OR Apache-2.0) AND Unicode-3.0` があります。ソースはcrate registryの同じ固定版から取得できます。

バイナリを別途配布する際は、実targetの推移依存を含む著作権・ライセンス本文とUnicode noticeを添付する必要があります。今回バイナリは公開していないため、バイナリ配布のnotice一式が完成したとは扱いません。
