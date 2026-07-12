# 基本的な利用方法

このページでは、StackChan_EX_Amigo を使い始めるための最小手順を説明します。

M5Stack / PlatformIO / SDカード設定など、AI_StackChan_Ex と共通する基本的な導入手順は、以下を参照してください。

- AI_StackChan_Ex 基本的な利用方法:
  https://github.com/ronron-gh/AI_StackChan_Ex/blob/main/doc/basic_usage.md

StackChan_EX_Amigo では、AI_StackChan_Ex をベースに、ローカル server 連携、Vision、push 型通知、Config UI、キャッシュ音声機能などを追加しています。

## Amigo版で追加・変更した点

- `server` 経由で LLM / STT / TTS / Vision を扱います
- Apple Silicon では MLX によるローカル LLM / VLM を利用できます
- Config UI からペルソナ、Vision キーワード、キャッシュ音声などを調整できます
- CoreS3 のカメラを使い、`見て` / `撮影して` / AI判断による視覚確認に対応します
- 会話ログや要約は server 側で管理します

## 導入の流れ

1. AI_StackChan_Ex の基本手順に従って、PlatformIO と SDカード設定を準備する
2. このリポジトリを clone する
3. `server/json_example/` を `server/json/` にコピーする
4. `.env.example` を `~/env/.env` にコピーして編集する
5. `server/voice_server.py` と `server/app.py` を起動する
6. `http://<server-ip>:5050/admin` を開き、Config UI で設定する（`ADMIN_TOKEN` が必要。未設定時は localhost のみ）
7. firmware 側の `SC_ExConfig.yaml` で `base_url` を server に向ける
8. PlatformIO で firmware をビルドして M5Stack に書き込む

## firmware 側の最小設定

詳細は [server.md](server.md#7-firmware-側の接続例) を参照してください。

Amigo版では、firmware 側から同一 LAN 上の server を OpenAI 互換 endpoint として参照します。

```yaml
llm:
  type: 0
  model: "Local-model"
  base_url: "http://<server-ip>:5050"

tts:
  type: 2
  model: "Local-model"
  voice: "alloy"
  base_url: "http://<server-ip>:5050"

stt:
  type: 1
  model: "Local-model"
  base_url: "http://<server-ip>:5050"
```

### 3. ConfigUI 設定

Config UI の詳細は server.md を参照してください。

http://<server-ip>:5050/admin

### 3.3. Vision / カメラ連携（CoreS3のみ）

`StackChan_EX_Amigo` では、カメラは `server` と連携します。主な使い方は次のとおりです。

- `見て` 系の発話で一時確認
- `撮影して` 系の発話で資料保存
- AI が必要と判断した際に自動で視覚確認

詳細な流れや設定は [vision.md](vision.md) を参照してください。

## 注意

- 自宅 server の外部公開は推奨していません
- incbin/personalize は非推奨です
- Vision / カメラ連携は CoreS3 前提です