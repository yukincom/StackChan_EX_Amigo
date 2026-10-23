# StackChan_EX_Amigo

## このプロジェクトについて

`StackChan_EX_Amigo` は、ｽﾀｯｸﾁｬﾝEXをベースに、ローカル LLM / VLM、Vision、TTS、CONFIG UI 連携などを追加した非公式のコミュニティ改造版です。

このプロジェクトは、robo8080さんの [AIｽﾀｯｸﾁｬﾝ](https://github.com/robo8080/AI_StackChan2)、ronron-ghさんの [AI_StackChan_Ex](https://github.com/ronron-gh/AI_StackChan_Ex)をベースに、 `Stack-chan` / AI Stack-chan コミュニティで積み重ねられてきた実装と知見の上に成り立っています。

ｽﾀｯｸﾁｬﾝは、ししかわ氏が開発・公開している手乗りサイズのコミュニケーションロボットです。  
ｽﾀｯｸﾁｬﾝ公式リポジトリ: https://github.com/stack-chan/stack-chan

## 主な特徴

- ローカル`server` で `chat / stt / tts / 画像解析`の処理が完結 
- Apple Silicon では `MLX` を使ったローカル LLM / VLM を利用可能
- CoreS3の顔長押しで入力方法を切り替え
  - `タッチ`: 一回ずつ会話
  - `いつでも`: 対応serverと連携して、毎回のウェイクワードなしで順次待受
- Vision / カメラ連携
  - `見て` 系は一時確認 (`transient`)
  - `撮影して` 系は保存前提 (`archive`)
  - AI 自身が必要と判断したときのカメラ起動に対応
- AI 発カメラ起動の挙動は、`server` 側の会話プロンプトと使う `LLM` の組み合わせで調整可能
- `today.md` / `archive` / 起動時バッチ要約によるログ運用
- SDカード キャッシュ音声とCONFIG UI からのキャッシュ音声運用
- LINE通知を署名検証Webhookから暗号化Queueへ受け、自宅Macが60秒ごとにpullする経路（自宅port公開・firmware変更なし）

## 実機デモ

サムネイルをクリックすると X の動画が開きます。

| ノイズのある環境での `いつでも` モード | 会話の文脈を使った Web 検索 |
| :---: | :---: |
| [![ノイズのある環境で動作するｽﾀｯｸﾁｬﾝ](https://pbs.twimg.com/amplify_video_thumb/2098668085968617472/img/y5TKIEhWlCwgFrz9?format=webp&name=medium)](https://x.com/yukin_co/status/2098668120043094304) | [![会話の文脈から検索して答えるｽﾀｯｸﾁｬﾝ](https://pbs.twimg.com/amplify_video_thumb/2098669663425376256/img/wU3H8M_n34zsfTkZ?format=webp&name=medium)](https://x.com/yukin_co/status/2098669744547381528) |

## リポジトリ構成

- `firmware/`
  - M5Stack 側 firmware（PlatformIO）
- `server/`
  - chat / stt / tts、Vision、CONFIG UI、会話ログ、定時処理
- `Copy-to-SD/`
  - SD カードへコピーする YAML、定型音声、アプリ設定
- `docs/`
  - Amigo版の使い方と補足ドキュメント

## 初期セットアップ

### 1. 環境変数を配置

```bash
mkdir -p ~/env
cp /path/to/StackChan_EX_Amigo/.env.example ~/env/.env
```
このリポジトリでは、AIエージェント対策として、環境変数ファイルを `~/env/.env` に置く運用をしています。

#### ADMIN_TOKEN（ConfigUI 認証・必須推奨）

LAN 上の他端末から `http://<server-ip>:5050/admin` を開く場合は **必須** です。未設定だと **localhost のみ** API が使えます。

```bash
# トークン生成
openssl rand -hex 32

# ~/env/.env または ~/env/.env.local に追記（値は生成結果）
ADMIN_TOKEN=ここに生成した文字列
```

server（`app.py`）を再起動したあと、ブラウザの ConfigUI で同じ文字列を入力します。  
詳細は [docs/server.md](docs/server.md) の「ConfigUI 認証」を参照してください。

詳細設定は ConfigUI から編集可能です（API キー等の password 項目は GET で生値を返しません）。

### 2. `json_example` を実運用用へフォルダ名変更

実運用では `server/json/` を使います。
`server/json_example/` を `server/json/` にコピーしてください。

```bash
cp -r server/json_example server/json
```

### 3. SD カードへファイルを配置

以下を SD カードへコピーします。

- `Copy-to-SD/yaml/SC_BasicConfig.yaml` -> `/yaml/SC_BasicConfig.yaml`
- `Copy-to-SD/yaml/SC_SecConfig.yaml` -> `/yaml/SC_SecConfig.yaml`
- `Copy-to-SD/app/AiStackChanEx/SC_ExConfig.yaml` -> `/app/AiStackChanEx/SC_ExConfig.yaml`
- `Copy-to-SD/stack_sd_audio/*.mp3` -> `/stack_sd_audio/`

### 4. 外部依存を準備

必須:

- `whisper.cpp`
- Japanese whisper model (`WHISPER_MODEL`)
- Silero VAD model for whisper.cpp (`WHISPER_VAD_MODEL`)
- `VOICEVOX`
- `ffmpeg`
- `kokoro`
- `Basic-Memory`
　-> https://github.com/basicmachines-co/basic-memory

構成に応じて使用:

- `mlx-lm`
- `mlx-vlm`

## 起動手順

Python環境は certifi >= 2026.10.19、uv >= 0.13.4（pip使用時は >= 26.2.2）が必須です。
初回・旧環境からの更新は `python3.11 tools/bootstrap.py` の後、
`.bootstrap-venv/bin/uv sync --frozen --no-python-downloads --group test` を実行してください。
詳細は [安全版への移行手順](docs/2026-10-23-emsign-security.md) を参照してください。

### 1. `VOICEVOX`　を起動

### 2. `voice_server` を起動

```bash
cd server
../.venv/bin/python voice_server.py
```

### 3. `server` を起動

```bash
cd server
../.venv/bin/python app.py
```

### 4. CONFIG UI を開く

`http://<server-ip>:5050/admin`

初回は `ADMIN_TOKEN` の入力を求められます（上記「環境変数を配置」参照）。

ここから以下を調整できます。

- アシスタント名 / ペルソナ
- メンバー情報
- 読み上げ辞書
- Vision キーワード
- キャッシュ音声
- AI / 音声 / M5Stack 接続設定

AI 発カメラ起動も、ここで設定するキーワードや AI 設定に加えて、`server` 側の会話プロンプトと使う `LLM` の特性で挙動が変わります。

### 5. firmware をビルド

`firmware/` を PlatformIO で開いて書き込みます。

## 入力モード（CoreS3）

顔のタッチ領域を2秒長押しし、いったん指を離してから `タッチ` または `いつでも` を選びます。

`いつでも` には対応serverと、音声認識・読み上げの設定が必要です。選択は保存されず、別アプリへ移ると停止します。StackChanが話している途中の割込みには対応していません。

詳しい使い方と現在の制約は [入力モード（ドラフト）](docs/input-modes.md) を参照してください。

## よく使う保存先

- 実運用設定: `server/json/`
- 会話ログ: `server/memory/`
- アーカイブ: `server/archive/`
- 音声生成物: `server/voice_store/`

詳細は [docs/server.md](docs/server.md) を参照してください。

## ドキュメント

- 基本手順: [docs/basic_usage.md](docs/basic_usage.md)
- 入力モード（ドラフト）: [docs/input-modes.md](docs/input-modes.md)
- server 構成: [docs/server.md](docs/server.md)
- Vision: [docs/vision.md](docs/vision.md)
- 実機なし保守ラボ: [docs/no-device-lab.md](docs/no-device-lab.md)
- ローカルserver運用: [docs/local-service-operations.md](docs/local-service-operations.md)
- Python 依存とライセンス: [docs/python-dependency-licenses.md](docs/python-dependency-licenses.md)
- LINE Queue relay: [docs/line-queue-relay.md](docs/line-queue-relay.md)

## 注意

- 本プロジェクトは非公式のコミュニティ改造版です
- 当プロジェクトでは自宅 `server` の外部公開は推奨していません
- 外出先での API 直結運用や単体運用は各自、必要に合わせて再構成してください。
- Vision / カメラ連携は CoreS3 前提です
- `CoreS3` のみ動作確認をしています。
