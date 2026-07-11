# PR 計画: Top 5 バグ修正（StackChan_EX_Amigo）

**日付**: 2026-07-11  
**根拠**: `doc/code-review-stacchan-ex-2026-07-11.md` の Fix-first priorities  
**方針**: 機能追加はしない。バグ / セキュリティ / データ破損 / 契約ズレのみ。  
**順序**: 依存関係が少ないものから、かつ「危険度 × 影響範囲」が高い順。  
**ブランチ命名**: `fix/pr01-admin-auth` … `fix/pr05-concurrency`

---

## 全体像

| PR | タイトル | 主対象 | Review Issues | 推定規模 |
|----|----------|--------|---------------|----------|
| **PR1** | Admin 認証 + シークレットマスク | server | 1, 2, 23 | S–M |
| **PR2** | SSRF / パストラバーサル硬化 | server (+ ファーム任意) | 3, 4, 5, (10) | M |
| **PR3** | `today.md` 単一ライターロック | server | 6 | S |
| **PR4** | 英語 STT 契約修正 | server (+ ファーム任意) | 7 | S |
| **PR5** | MLX/VLM 同時実行 + Vision キュー | server (+ ファーム任意) | 8, 9, (11–13) | M |

**推奨スタック**: PR1 → PR2 → PR3 → PR4 → PR5（直列マージ）。  
PR3 / PR4 は PR1–2 とファイル衝突が少ないが、レビュー負荷を抑えるため **1 本ずつ main に入れる** 想定。

**今回スコープ外**（別イテレーション）:
- lipSync / Watchdog / 電源診断（UX・ハード寄り）
- Discord 複数メッセージ取りこぼし（Issue 14）
- announcements.json import 時クラッシュ（Issue 15）— 触るなら PR1 後の小 PR でも可
- firmware `/play` 非同期化・JSON 2KB 拡大（Issue 11, 13）— PR5 の「任意」または PR6

---

## PR1 — Admin 認証 + シークレットマスク

### 目的
LAN 上の誰でも ConfigUI から API キーを読み書きできる状態を止める。

### 変更対象（予定）
- `server/admin_routes.py` — 全 `/admin` / `/admin/api/*` に認証
- `server/config.py` または `~/env/.env.example` — `ADMIN_TOKEN` / `ADMIN_PASSWORD` 追加
- `server/templates/admin/index.html` + `server/static/admin/js/*` — トークン送信（Header または Basic Auth）
- `docs/server.md` — 初回セットアップ手順

### 設計案（推奨）
1. **共有トークン方式**（実装が軽い）
   - env: `ADMIN_TOKEN=<ランダム文字列>`
   - 全 admin ルート: `Authorization: Bearer <token>` または `X-Admin-Token`
   - 未設定時: 開発用に **localhost のみ許可**、それ以外は 401（「開けっぱなし」を防ぐ）
2. **GET レスポンスのマスク**
   - `type: password` のキーは `value` を返さない / `********` + `is_set: true/false`
   - POST で空文字 or `********` は「変更なし」扱い（上書きでキー消しを防ぐ）
3. **MLX download**（Issue 23）
   - 同じ admin 認証必須
   - モデル ID は簡易 allowlist または `^[A-Za-z0-9._/-]+$` + 長さ上限

### 受け入れ条件
- [ ] トークンなしで `/admin` と `/admin/api/env` が 401
- [ ] トークン付きで ConfigUI が従来どおり使える
- [ ] GET で `OPENAI_API_KEY` / `GEMINI_API_KEY` / `DISCORD_BOT_TOKEN` の生値がレスポンスに出ない
- [ ] POST でキーを触らず他項目だけ保存しても API キーが消えない
- [ ] `docs/server.md` にトークン設定手順がある

### 手動テスト
```bash
# 拒否
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5050/admin/api/env
# 許可
curl -s -H "X-Admin-Token: $ADMIN_TOKEN" http://127.0.0.1:5050/admin/api/env | jq .
```

### リスク / 注意
- 既存ユーザーは `.env` に `ADMIN_TOKEN` を足す必要がある（破壊的変更をドキュメントで明示）
- ブラウザの localStorage にトークンを置く場合は XSS に注意（当面は sessionStorage 推奨）

---

## PR2 — SSRF / パストラバーサル硬化

### 目的
任意 URL 取得・任意ファイル読み書きを閉じる。

### 変更対象（予定）
- `server/openai_compat_routes.py` — `/audio/proxy.mp3`
- `server/services/voice_cache_catalog.py` — `get_pc_cache_audio_path` / `get_stack_sd_audio_path`
- `server/voice_server.py` — `/voice/<id>`, `/song/<name>`
- `server/voice_server/cache_manager.py` — キャッシュキー検証
- （任意）`firmware/src/driver/PlayMP3.cpp` — ダウンロードサイズ上限（Issue 10）

### 設計案

#### 2a. audio proxy SSRF
- `src` を `urllib.parse` で解析
- 許可: scheme `http` のみ、host が `localhost` / `127.0.0.1` / 設定の voice_server host、port が voice_server ポート
- path は `/voice/` または `/song/` プレフィックスのみ
- それ以外は 400

#### 2b. ファイル名 sanitization
共通ヘルパ例:
```python
def safe_id(value: str, *, max_len: int = 64) -> str | None:
    v = (value or "").strip()
    if not v or len(v) > max_len:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", v):
        return None
    return v
```
- `get_*_audio_path`: `safe_id` 後に `resolve()` して `base.resolve()` 配下か `is_relative_to` で検証
- `voice_id` / `song_name` も同様（`cache_` プレフィックスは strip 後に検証）

#### 2c. PlayMP3 上限（任意・同 PR か PR5 後）
- Content-Length / 累計バイトが N（例 1MB）超で abort
- 失敗時は再生せずエラーログ

### 受け入れ条件
- [ ] `src=http://169.254.169.254/` や `src=http://evil.example/` が 400
- [ ] 正常な voice_server URL の proxy は従来どおり MP3 を返す
- [ ] `filename=../../etc/passwd` や `song_name=../secrets` が 400 / 404
- [ ] 既存キャッシュ ID（`noise_check` 等）は引き続き動く

### 手動テスト
```bash
# SSRF 拒否
curl -s "http://127.0.0.1:5050/audio/proxy.mp3?src=http://127.0.0.1:22/" | jq .
# トラバーサル拒否
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:5001/song/../../etc/passwd"
```

### リスク / 注意
- `VOICE_SERVER_URL` が `http://192.168.x.x:5001` の場合、allowlist にその host を含めること
- ファイル名規則を厳しすぎると既存カタログ ID が壊れる → 現状の ID 文字種を事前に確認

---

## PR3 — `today.md` 単一ライターロック

### 目的
会話ログの欠損・破損を止める。

### 変更対象（予定）
- `server/memory_manager.py` — 読み書き API の中心化 + `threading.RLock`
- `server/services/weather_service.py` — 直接 `open(today.md)` している箇所を memory API 経由へ
- `server/services/batch_service.py` — 同上
- `server/services/vision_handler.py` — `RobotMemory` 経由なら lock 内に取り込む

### 設計案
1. `memory_manager.py` にモジュールレベル `_TODAY_LOCK = threading.RLock()`
2. 公開 API:
   - `read_today() -> str`
   - `append_today(text: str)`
   - `replace_today(text: str)` または `update_section(section_title, body)`
3. weather / batch の生 `open` を上記に寄せる
4. batch の長い rewrite も同一ロック内（その間 chat append は待つ — 正しさ優先）
5. （任意）起動時 batch 完了後にリクエスト受付、は挙動変更が大きいので **今回はロックのみ**

### 受け入れ条件
- [ ] `RobotMemory._append` / weather 更新 / batch 更新が同じロックを取る
- [ ] 並行で chat と weather を叩いても `today.md` が壊れない（簡易ストレステスト可）
- [ ] 既存の会話・天気・要約のフォーマットが変わらない

### 手動テスト
```bash
# 並行 append 相当: 複数ターミナルで chat を連打しつつ天気更新ジョブを走らせる
# 事後: today.md の見出し・リストが壊れていないこと
```

### リスク / 注意
- ロック保持中に LLM 呼び出しをすると全会話がブロック → **ファイル I/O だけ** をロック内に
- batch の LLM 要約はロック外で計算し、書き込みだけロック内

---

## PR4 — 英語 STT 契約修正

### 目的
ファームが実際に叩く `/v1/audio/transcriptions` で英語モードを効かせる。

### 変更対象（予定）
- `server/openai_compat_routes.py` — `transcriptions()`
- （確認のみ）`server/app.py` の `/speech/transcribe` は既に `is_english_mode()` 利用
- （任意）`firmware/src/stt/Whisper.cpp` — `language=ja` ハードコード緩和

### 設計案
```python
# transcriptions()
from ai_handler import is_english_mode

use_en = is_english_mode()
# form language はヒント。英語モード中は EN モデル優先
language = request.form.get("language", "en" if use_en else "ja")
transcript = speech_service.transcribe(
    audio_content,
    language_code=language,
    use_english_model=use_en,
)
```
- 英語モード OFF 時は現状どおり（form `language` デフォルト `ja`）
- 英語モード ON 時は `use_english_model=True`（`WHISPER_MODEL_EN` 未設定ならフォールバック + ログ）

### 受け入れ条件
- [ ] 英語モード ON 後、デバイス経由 STT が EN モデル / `en` で走るログが出る
- [ ] 日本語モードに戻すと JA モデルに戻る
- [ ] `/speech/transcribe` と `/v1/audio/transcriptions` の言語判定が一致

### 手動テスト
1. ConfigUI または会話で英語モード ON
2. CoreS3 から発話 → server ログで `WHISPER_MODEL_EN` / `lang=en` を確認
3. 日本語に戻して再確認

### リスク / 注意
- プロセス全体の英語フラグ（Issue 20）は残る — 今回は「効かない」バグだけ直す
- ファームの `language=ja` 固定は、server が `use_english_model` で上書きすれば当面 OK

---

## PR5 — MLX/VLM 同時実行 + Vision キュー

### 目的
MLX クラッシュ / メモリ膨張 / Vision 嵐を防ぐ。

### 変更対象（予定）
- `server/llm_client.py` — generate 全体をロック（load だけでなく call）
- `server/app.py` — `/vision/upload` を無制限 Thread からキューへ
- （任意・別コミット可）firmware:
  - `WebAPI.cpp` / `main.cpp` — play/camera を queue + 非同期（Issue 11）
  - `ChatGPT.cpp` — JSON ドキュメントサイズ拡大（Issue 13）
  - `AudioWhisper.cpp` — malloc NULL チェック（Issue 12）

### 設計案（server 必須部分）
1. `llm_client.py`
   - `_mlx_generate_lock = threading.Lock()`
   - `_call_mlx_text` / `_call_mlx_vlm` の load+generate を同一ロックで囲む  
     （load ロックと二重にしないよう整理）
2. Vision
   - `queue.Queue(maxsize=1)` + 単一 worker スレッド
   - 満杯時は 429 または「処理中なので少し待って」応答
   - 古い transient は drop-oldest でも可

### 受け入れ条件
- [ ] 並行 chat + vision で MLX が二重 generate しない（ログで直列化を確認）
- [ ] vision 連打でスレッド数が増えない
- [ ] 通常の 1 枚 vision は従来どおり応答する

### 手動テスト
- 端末から「見て」連打 + 同時に通常会話
- server のスレッド数 / エラーログ / 応答内容を確認

### リスク / 注意
- generate ロックはレイテンシが直列になる — 正しさ優先で OK
- firmware 非同期化は回帰リスクが高い → **PR5 は server のみ**、firmware は PR5b 推奨

---

## 推奨 PR5 分割（実運用）

| サブ | 内容 | 必須? |
|------|------|-------|
| **PR5a** | MLX generate ロック + vision キュー | 必須 |
| **PR5b** | ChatGPT JSON サイズ + AudioWhisper NULL チェック | 推奨 |
| **PR5c** | play/camera 非同期化 | 後回し可（回帰大） |

Top 5 の「5」としては **PR5a でクローズ**し、5b/5c は続くバグ修正イテレーションでもよい。

---

## マージ順と依存

```text
main
  └─ PR1 admin-auth          ← 最初。秘密露出を止める
       └─ PR2 path-ssrf      ← admin の generate 経路も safe_id を使う
            └─ PR3 today-lock
                 └─ PR4 en-stt
                      └─ PR5a mlx-vision-queue
                           ├─ PR5b firmware-safety (optional)
                           └─ PR5c firmware-async-play (optional)
```

PR3 と PR4 は互いにほぼ独立だが、**レビューと bisect のため直列**を推奨。

---

## 各 PR 共通ルール

1. **機能追加しない**（UI は認証・マスクに必要な最小限のみ）
2. **秘密をログに出さない**
3. 変更は server 中心。firmware は PR2 任意と PR5b/c のみ
4. PR 説明に Review Issue 番号を書く
5. マージ前チェックリスト:
   - [ ] 手元で app.py + voice_server 起動
   - [ ] 通常会話 1 往復
   - [ ] 当該 PR の受け入れ条件を満たす

---

## 着手時の最初の 3 コミット案（PR1）

1. `config`: `ADMIN_TOKEN` 読み込み + 未設定時のポリシー
2. `admin_routes`: before_request 認証 + password マスク + POST の「変更なし」
3. admin UI: トークン入力 / Header 付与 + docs 更新

---

## 次のアクション

ユーザー承認後:

1. `fix/pr01-admin-auth` ブランチ作成
2. PR1 実装 → 動作確認 → コミット
3. 同様に PR2… と進める

承認ポイント（決めておくと実装が速い）:
- Admin 認証: **Bearer / X-Admin-Token** でよいか
- トークン未設定時: **localhost のみ許可** でよいか（完全拒否でも可）
- PR5 は **5a のみ**で Top5 完了とするか、5b まで含めるか
