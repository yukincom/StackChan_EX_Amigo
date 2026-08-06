# 次にやるべきこと（2026-07-13 時点）

**方針**: 日常開発・実機確認は **yuno-chan-api**（GitHub: `dev_StackChan_EX_Amig`）。  
**StacChan_EX_Amigo** は整理用・firmware 付き。ハード変更は溜めてフラッシュ回数を減らす。

---

## いま完了していること（server）

| 項目 | yuno | Amigo |
|------|------|-------|
| PR1 Admin 認証 / シークレットマスク | ✅ | ✅ |
| PR2 SSRF / パストラバーサル | ✅ | ✅（firmware PlayMP3 上限は Amigo のみ） |
| PR3 today.md ロック | ✅ | ✅ |
| PR4 英語 STT + 英語モード LLM | ✅ 実機確認 | ✅ 移植済み |
| PR5a MLX generate ロック + Vision キュー | ✅ | ✅ |
| ADMIN_TOKEN 運用・ドキュメント | ✅ 設定済み | ✅ `.env.example` / README / `CLAUDE.md` / `docs/server.md` |
| 天気質問→カメラ誤爆修正（「今日の天気は何？」） | ✅ | ✅ |

**server 側の Top5 + 英語 + 天気誤爆は一通りクローズ。**

---

## すぐやるとよいこと

### 1. 未コミットの確定（優先・低リスク）

- **yuno**: 天気→カメラ誤爆修正など、未 commit があれば commit
- **Amigo**: PR4/5a 移植・ADMIN_TOKEN ドキュメント・天気誤爆など未 commit があれば commit

区切りを Git に残してから様子見に入る。

### 2. 様子見（運用）

- 日常会話・天気・「見て」・英語モードを数日回す
- 変な挙動は **yuno の server ログ**で切り分け（フラッシュ不要のことが多い）
- 診断ログ（`[STT]` / `[SPEECH]`）がうるさければ間引きを検討

---

## 次の改善候補（優先度順）

### A. server（yuno で直す・フラッシュ不要）

| 優先 | 内容 | メモ |
|------|------|------|
| 中 | Discord 複数メッセージ取りこぼし | レビュー Issue 14 |
| 中 | `announcements.json` 破損時の起動クラッシュ | レビュー Issue 15 |
| 低 | STT 診断ログの間引き / env ゲート | 運用が落ち着いたら |
| 低 | VLM を MLX generate ロックと揃える | PR5a の延長 |
| 低 | dead code / コメント掃除 | 気が向いたら |

### B. firmware 束（Amigo・フラッシュをまとめる）

溜めてから 1 キャンペーンで入れる。

| 内容 | メモ |
|------|------|
| lipSync 改善（更新頻度・RMS） | UX |
| Watchdog 再有効化 | ハング対策 |
| `/play`・カメラの loop 非同期化 | 応答性 |
| 電源・充電のログ / ポリシー | 電源落ち調査 |
| その他ハード関連 | サーバで代替できるものはサーバ優先 |

### C. 大きな話（急がない）

- 外出先アクセス（Tailscale 等）— ADMIN_TOKEN の外側の門
- 長期記憶の強化
- yuno ↔ Amigo の server 同期手順の固定化
- デバイス API の LAN トークン（ファーム変更が必要なら B に含める）

---

## 作業場所の覚え書き

```text
yuno-chan-api     = 自宅運用・本開発（server のみ）  remote: dev_StackChan_EX_Amig
StacChan_EX_Amigo = 公開/整理・firmware 付き         remote: StackChan_EX_Amigo
AI_StackChan_Ex   = 上流 Ex ファーム寄り
```

- サーバだけで完結 → **yuno**
- ハードが絡む → **メモして後で Amigo firmware 束**
- 固まった server 修正 → 必要なら Amigo に移植

---

## おすすめの進め方（この先しばらく）

1. **commit で区切る**（上記「すぐやるとよいこと」）
2. **様子見**（日常運用）
3. 不具合が出たら **yuno の server から直す**
4. 口パク・電源などが気になり始めたら **firmware 束（B）**

---

## 関連ドキュメント

- `doc/code-review-stacchan-ex-2026-07-11.md` — フルレビュー
- `doc/pr-plan-top5-bugfix-2026-07-11.md` — Top5 PR 計画
- `CLAUDE.md` — エージェント向け（ADMIN_TOKEN 等）
- `docs/server.md` — ConfigUI / ADMIN_TOKEN 手順
