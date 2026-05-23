# server 連携

このページでは、`StackChan_EX_Amigo` の `server` 連携について説明します。  
Amigo版では、会話・Vision・ログ運用・Config UI の中心は `server` 側です。

## 1. server の主な役割

`server` では、主に次の機能を担当します。

- MLXをはじめとするLLMとの接続
- M5Stack への push 型音声再生
- Vision / カメラ連携
- 会話ログ、`today.md`、アーカイブ、起動時要約
- 複数ユーザー判定
- 名前マスク
- 設定Config UI
- 定時アナウンス
- 天気予報取得
- tool functionからのLLM回答生成

## 2. 構成の考え方

基本構成は次のとおりです。

- M5Stack は音声入出力とカメラ、表情制御を担当
- `server/app.py` は会話、STT、Vision、Config UI、ログ運用を担当
- `server/voice_server.py` は音声生成を担当

会話の流れは概ね次のようになります。

1. M5Stack が音声を録音する
2. firmware から `server` の endpoint を呼ぶ
3. `server` 側で STT / 会話 / 補助機能 / Vision 判定などを処理する
4. `voice_server.py` で音声を生成する
5. `server` から M5Stack へ再生指示を push する

## 3. 事前に用意するもの

### 3.1. 設定ファイル

まず、サンプルの名称変更をしてください。

- `server/json_example/` → `server/json/`
- `.env.example` → `~/env/.env`

today.mdは以下のディレクトリに移動してください。

- `~/basic-memory/`

その後、`http://(serverのIPアドレス):5050/admin`にて`~/env/.env` の内容を自分の環境に合わせて編集してください。

### 3.2. 外部依存

構成に応じて、次の外部依存を別途用意します。

必須:

- `VOICEVOX`
  - 日本語音声の生成に使用します
- `ffmpeg`
  - 音声変換やキャッシュ生成に使用します
- `whisper.cpp`
  - 音声認識に使用します
- 日本語 / 英語の Whisper モデル
- `kokoro`
  - 英語音声や日英混在音声を使う場合
- `mlx-lm`
  - ローカル LLM を使う場合
- `mlx-vlm`
  - ローカル Vision を使う場合


ダウンロードや導入は、それぞれの公式配布元・公式リポジトリに従ってください。

## 4. 主な設定項目

`http://(serverのIPアドレス):5050/admin`　にて編集してください。

### 4.1. AI 関連

```env
AI_PROVIDER=gemini
AI_CHAT_MODEL=gemini-2.5-flash
AI_SUMMARY_PROVIDER=gemini
AI_SUMMARY_MODEL=gemini-2.5-flash
VLM_MODEL=lmstudio-community/Qwen3-VL-8B-Instruct-MLX-4bit
```

`AI_PROVIDER=mlx` にすると、ローカル MLX を利用できます。  。

### 4.2. M5Stack 接続

```env
M5STACK_IP=192.168.1.49
M5STACK_PORT=80
M5STACK_URL=http://192.168.1.49:80
```

`server` は M5Stack に対して push 型で再生指示やカメラ起動指示を送るため、`M5STACK_IP` と `M5STACK_URL` は重要です。  
M5Stack の IP は、手動固定よりもルーターの DHCP 予約で固定する運用をおすすめします。

### 4.3. 音声関連

```env
VOICE_SERVER_URL=http://127.0.0.1:5001
VOICEVOX_URL=http://127.0.0.1:50021
VOICEVOX_SPEAKER_ID=2
KOKORO_VOICE_ENGLISH=af_sarah
```

- 日本語 TTS は `VOICEVOX`
- 英語 TTS は `KOKORO_VOICE_ENGLISH`

という構成です。

### 4.4. STT 関連

```env
WHISPER_CLI=
WHISPER_MODEL=
WHISPER_MODEL_EN=
```

`WHISPER_CLI` に `whisper.cpp` の実行ファイル、`WHISPER_MODEL` / `WHISPER_MODEL_EN` にモデルファイルを設定します。

## 5. 起動

`server` 側では、少なくとも次の 2 プロセスを起動します。

```bash
cd server
python voice_server.py
```

```bash
cd server
python app.py
```

標準では次のポートを使います。

- `voice_server.py`: `5001`
- `app.py`: `5050`

## 6. Config UI

Config UI は次の URL で開けます。

```text
http://(serverのIPアドレス):5050/admin
```

日常的な調整は、基本的にこのConfig UI から行います。主に触るのは次の項目です。

- アシスタント名
- ペルソナ
- メンバー情報
- 読み上げ辞書
- Vision キーワード
- AI / 音声 / M5Stack 接続設定
- キャッシュ音声

`incbin` の `personalize` 機能は firmware 側に残っていますが、Amigo版では `server` のConfig UI を使ってください。

## 7. firmware 側の接続例

`SC_ExConfig.yaml` では、M5Stack に　endpoint を参照させます。

```yaml
llm:
  type: 0
  model: "Local-model"
  base_url: "http://192.168.1.10:5050"

tts:
  type: 2
  model: "Local-model"
  voice: "alloy"
  base_url: "http://192.168.1.10:5050"

stt:
  type: 1
  model: "Local-model"
  base_url: "http://192.168.1.10:5050"
```

`Local-model` はダミーです。変更や消去の必要はありません。  
実際に使う LLM / STT / TTS は `server` 側の設定で決まります。

## 8. 保存先

主な保存先は次のとおりです。

- `server/json/`
  - 実運用の設定 JSON
- `server/json_example/`
  - 個人設定用サンプル server/jsonに名称変更してください。
- `server/memory/`
  - `today.md` や要約
- `server/archive/`
  - 日別アーカイブ
- `server/voice_store/`
  - 音声生成物

## 9. 注意

- Amigo版では、自宅 `server` の外部公開は推奨していません
- 外出先からの単体運用や API 直結運用は、各自で構成してください
- `incbin/personalize` は残していますが、Amigo版では非推奨です
