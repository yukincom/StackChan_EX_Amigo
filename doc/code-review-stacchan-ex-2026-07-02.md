# コードレビュー: StackChan_EX_Amigo 改修計画

**日付**: 2026-07-02  
**レビュワー**: Grok Build
**プロジェクト**: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo` (Stackちゃん EX Amigo)  
**レビュー対象**: firmware (C++/PlatformIO on M5Stack CoreS3 等) + server (Python)  
**方針**: コードは一切編集せず。レビュー + 修正計画（diff例含む）に集中。  
**保存先**: 本ファイルは `/doc/` 配下（公開用の `/docs/` とは明確に分離）。

## プロジェクト概要（レビュー時点）

- **firmware/**: M5Stack CoreS3 (主) / Core2 向け。Avatar表示、サーボ、マイク入力、WakeWord、TTS再生、カメラなどを制御。
- **server/**: Python (Flask) + 別プロセス voice_server。whisper.cpp STT、LLM/TTS連携、Vision、ログ管理。
- 特徴: ローカル完結志向、SDカード設定、CONFIG UI。
- 現在のユーザー体験ボトルネック: 電源落ち、口パクの弱さ、ウェイクワード中心の操作性、なでなで誤反応。

---

## 1. コードクリーンアップ（最優先）

### 現状と問題

**firmware (C++)**
- 条件コンパイル多用: `#if defined(ENABLE_WAKEWORD)`, `#if defined(ENABLE_TAP_DETECT)`, `#if defined(ENABLE_CAMERA)` など。
- platformio.ini で多くの機能がコメントアウト (`;-DENABLE_TAP_DETECT`)。
- WatchDog の初期化/リセットが main.cpp でコメントアウトされている。
- lipSync がカスタム実装で m5stack-avatar ライブラリの `tasks/LipSync.h` と競合気味。
- DefaultParams.h に重複定義あり (DEFAULT_SPEAKER_VOLUME が複数回)。
- 古いデバッグコード、未使用の include や変数散見（例: 古い SPIFFS 関連、raw バッファ関連のコメントアウトコード）。
- いくつかの Mod (Pomodoro, PhotoFrame など) が実装されているが、CoreS3 デフォルトでは一部使われていない可能性。

**server (Python)**
- 静的解析ツール未導入のため dead code は手動レビュー必要。
- speech_service.py 内に大量のデバッグコメントが残っている。
- いくつかのルートやサービスで重複したエラーハンドリング。
- `today.md` が複数箇所 (root, services, templates) に存在。
- 未使用インポートや関数は現時点で致命的ではないが、徐々に蓄積。

### 推奨アクションとツール

**Python 側（即実行推奨）**
```bash
cd server
pip install vulture flake8
vulture . --min-confidence 80 > deadcode.txt
flake8 . --count --select=E9,F63,F7,F82 --show-source
```

**C++ 側**
- cppcheck / clang-tidy を導入（PlatformIO で可能）。
- 手動で `#if 0` や大規模コメントブロックを削除。

**提案 diff（例: main.cpp 内の不要コメント・重複整理）**

```diff
diff --git a/firmware/src/main.cpp b/firmware/src/main.cpp
index ...
--- a/firmware/src/main.cpp
+++ b/firmware/src/main.cpp
@@
-//init_watchdog();
+// Watchdog は現在無効化中。将来的に復活させる場合はここを有効化
+// init_watchdog();
 
-  //reset_watchdog();
+  // reset_watchdog();   // 現在無効
```

```diff
diff --git a/firmware/src/share/DefaultParams.h b/firmware/src/share/DefaultParams.h
index ...
--- a/firmware/src/share/DefaultParams.h
+++ b/firmware/src/share/DefaultParams.h
@@
-#define DEFAULT_SPEAKER_VOLUME  (120)
-#define DEFAULT_SPEAKER_VOLUME  (120)
-#define DEFAULT_SPEAKER_VOLUME  (200)
+#define DEFAULT_SPEAKER_VOLUME  (120)   // Core2 / 基本
```

**全体クリーンアップ計画**
1. vulture + 手動レビューで Python dead code 削除 PR。
2. firmware の条件コンパイルを整理（feature flag ドキュメント化）。
3. コメントアウトされた watchdog を削除 or 明確にドキュメント化。
4. lipSync のカスタム実装をライブラリ標準と統合検討。

---

## 2. 電源落ち問題の調査・改善

### 現状

- `battery_check` タスク（main.cpp）が 定期的に `M5.Power.getBatteryLevel()` を呼んでアイコン表示するのみ。
- 充電中判定: `M5.Power.isCharging()`
- 電源制御: `M5.Power.setExtOutput(!config.getUseTakaoBase())` （Robot.cpp）
- 意図的な電源OFF: `M5.Power.powerOff()` （FunctionCall.cpp の shutdown タイマー）
- Config に `auto_power_off_time` あり（Core2 のみ？）。
- Watchdog は実装されているが main で無効化。
- 1日つけっぱなしで落ちる → **ハードウェアの過充電保護が最も有力**。

### 分析（ハード / ソフト切り分け）

**ハードウェア要因（有力）**
- M5Stack の電源IC (IP5306 など) は長時間充電状態で過充電保護でオフになる設計が多い。
- TakaoBase などの外部給電構成で挙動が変わる。
- ユーザーが「1日つけっぱなし」と報告 → 典型的な過充電保護パターン。

**ソフトウェア要因**
- コード側に**積極的な電源管理はほぼ存在しない**（スリープなし、充電電流制御なし、定期的な軽いタスクのみ）。
- battery_check は表示専用で、バッテリーロジックは薄い。
- 長時間稼働時の発熱や WiFi 常時接続が間接的に影響する可能性。

### 改善提案

1. **診断追加**（最優先）
   - 起動時に電源状態・充電時間・電圧をシリアル + ログに記録。
   - `M5.Power.getBatteryLevel()` だけでなく可能なら電圧も。

2. **ソフト側過充電対策**
   - 長時間充電検知で `setExtOutput(false)` や低消費モード移行。
   - アイドル長時間で軽いスリープや WiFi power save。

3. **ハード対策（ユーザー向け）**
   - 夜間は USB 抜く / スマートプラグで給電制御を推奨。
   - TakaoBase 使用時は設定を確認。

**提案コード例 (battery_check 強化)**

```diff
diff --git a/firmware/src/main.cpp b/firmware/src/main.cpp
--- a/firmware/src/main.cpp
+++ b/firmware/src/main.cpp
@@
 void battery_check(void *args) {
   ...
   for (;;) {
+    static unsigned long charge_start_ms = 0;
     int32_t batteryLevel = M5.Power.getBatteryLevel();
+    bool charging = M5.Power.isCharging();
+
+    if (charging) {
+      if (charge_start_ms == 0) charge_start_ms = millis();
+      if (millis() - charge_start_ms > 1000UL * 60 * 60 * 8) {  // 8時間以上充電
+        // ログ + 将来的に給電制御
+        Serial.println("[POWER] Long charge detected. Consider disconnecting.");
+      }
+    } else {
+      charge_start_ms = 0;
+    }
+
     if((batteryLevel < 95) && (batteryLevel != 0)){
       ...
```

---

## 3. 口パク動作の強化

### 現状（問題の核心）

**firmware/src/main.cpp** の `lipSync` タスク:

```cpp
void lipSync(void *args) {
  for (;;) {
    level = robot->tts->getLevel();
    if(level<100) level = 0;
    if(level > 15000) level = 15000;
    float open = (float)level/15000.0;
    avatar->setMouthOpenRatio(open);
    ...
    delay(100);   // ← これが致命的
  }
}
```

- `TTSBase::getLevel()` のデフォルト: `return abs(*out.getBuffer());` （1サンプルだけ）
- 更新間隔 100ms（10fps）→ 間延び・カクカク。
- 閾値処理が原始的（クリップ + リニアマップ）。
- m5stack-avatar ライブラリ標準の LipSync タスクがあるのにほとんど使われていない。

### 改善案

1. **更新レートを上げる**（20-30ms 程度、可能なら）。
2. **エンベロープ処理**（アタック/リリース、ピークホールド、低域通過フィルタ）。
3. **getLevel() を改善**して RMS や複数サンプル平均を返す。
4. 可能ならライブラリの LipSync をベースにカスタマイズ。

**提案 diff（lipSync 強化）**

```diff
diff --git a/firmware/src/main.cpp b/firmware/src/main.cpp
--- a/firmware/src/main.cpp
+++ b/firmware/src/main.cpp
@@
 void lipSync(void *args)
 {
   ...
   for (;;)
   {
-    level = robot->tts->getLevel();
-    if(level<100) level = 0;
-    if(level > 15000) level = 15000;
-    float open = (float)level/15000.0;
-    avatar->setMouthOpenRatio(open);
+    static float smoothed = 0.0f;
+    int raw = robot->tts->getLevel();
+    if (raw < 80) raw = 0;
+
+    // 簡易ローパス + リリース
+    float target = (float)raw / 15000.0f;
+    if (target > smoothed) {
+      smoothed = smoothed * 0.6f + target * 0.4f;  // attack
+    } else {
+      smoothed = smoothed * 0.85f + target * 0.15f; // release
+    }
+    avatar->setMouthOpenRatio(smoothed);
+
     delay(30);   // より高レートに
   }
 }
```

**TTSBase 改善提案**
`getLevel()` をオーバーライドしてより良い振幅を返す（OpenAITTS / PlayMP3 側で実装）。

---

## 4. 常時マイクオン化

### 現状

- 基本は **WakeWord (SimpleVox)** + 画面タッチ / ボタン。
- デバイス側で VAD + 録音 → サーバー whisper.cpp に送信。
- ウェイクワード登録機能（function calling 経由）あり。
- `speech_service.py` にノイズフィルタはある程度存在。

### 評価とリスク（常時オン化時）

| 項目             | 評価                          | 影響度 |
|------------------|-------------------------------|--------|
| ノイズ耐性       | 現在のパターン除去は弱い     | 高     |
| CPU負荷 (デバイス) | WakeWord より大幅増          | 高     |
| バッテリー       | 常時マイク+WiFi で激減       | 非常に高 |
| サーバー負荷     | 連続 whisper 呼び出し        | 高     |
| 誤認識           | 常に聞いているので大幅増     | 非常に高 |

### 推奨アーキテクチャ

**段階的アプローチ推奨（いきなりフル常時オンは危険）**

1. **VAD on device**（最優先）
   - デバイス側で簡易 VAD（エネルギー or 既存の simplevox VAD 活用）。
   - 音声あり区間のみサーバーに送信（チャンク単位）。

2. **サーバー側ストリーミング VAD**
   - デバイスは常に録音ストリーム（低レート）。
   - server で VAD して本気の whisper をトリガ。

3. **ハイブリッド**
   - デフォルトは低電力 VAD。
   - 「集中会話モード」時のみフル常時オン。

**実装提案（大枠）**

- 新しい `ContinuousListen` モードを追加。
- `ENABLE_ALWAYS_LISTEN` フラグで切り替え。
- ノイズ耐性: より強力なノイズフィルタ + 話者ダイアライゼーション的閾値。
- バッテリー: マイクゲイン下げる + WiFi power save + 間欠送信。

---

## 5. なでなで反応機能の実装

### 現状（リスク大）

現在の `TapDetect` は **ダブルタップ専用**（IMU加速度）:

- 閾値: `TAP_DELTA_MIN=0.4`, `MAX=0.9`
- 時間窓: 300〜1000ms
- 方向一致: `COS_SIMILAR = 0.9`（かなり厳しめ）
- タスク停止/再開で busy 時は検知オフ
- 現在 `doubleTapped` コールバックはほぼログ + ATOMS3R 限定で STT 開始

**問題点**
- 「なでなで」（連続的なストローク）はダブルタップ検知に合わない。
- 単発の振動・持ち上げ・歩行などで誤検知しやすい。
- CoreS3 一部モデルは IMU 非搭載。
- 誤反応で「挙動が壊れる」報告の原因になりやすい。

### ベストプラクティス提案

**誤検知防止を最重視**で実装:

1. **多段フィルタ**
   - ローパス + ハイパス
   - 振幅 + 持続時間 + 周波数成分で「なで」らしさを判定
   - デバウンス: 最小間隔（例: 800ms）

2. **コンテキスト考慮**
   - 話中・カメラ中・busy 時は完全に無効
   - 画面タッチと排他

3. **しきい値の外部化**
   - YAML / Config で調整可能に（現在はハードコード）

4. **段階的反応**
   - 軽くなで → 表情だけ
   - しっかり → 短いリアクション音声 + 表情

**実装推奨**
- 新規 `HeadPetSensor` クラス作成（TapDetect をベースに拡張 or 置き換え）。
- 現在は「ダブルタップ」として残しつつ、なでなでは別チャネルで。

---

## 6. サーバー側関連

### 現状の懸念点

- whisper.cpp を subprocess で同期呼び出し（speech_service.py）→ 同時会話時にボトルネック。
- バッチ・スケジューラ・通知が起動時に複数起動。
- Vision / VLM も重い処理。
- 音声合成は voice_server (別プロセス) と連携。

### 常時マイクオン時の影響

- whisper 呼び出しが爆増 → CPU / メモリ / 遅延悪化。
- 推奨: 
  - サーバー側 VAD キャッシュ
  - 軽量モデル or 量子化モデル併用
  - 非同期キュー + ワーカープール
  - レートリミット

### 改善提案

- speech_service に **サーバー側 VAD** を追加（webrtcvad など）。
- 現在進行中の長い会話は `voice_server` としっかり分離。
- ヘルスチェックとリソースモニタリング強化。

---

## 追加推奨（全体）

- **ドキュメント**: `/doc/` に内部設計メモを増やす（公開 docs/ とは分ける）。
- **設定の一元化**: 閾値類（タップ、lip、電源）はできるだけ config/yaml へ。
- **テスト**: 特に TapDetect / lipSync / 電源状態の単体テストがほぼない。
- **ログ**: 電源・センサー・音声レベルを時系列で残せる仕組みを追加。

---

## 優先実装順序（おすすめ）

1. **コードクリーンアップ**（vulture + 手動削除）
2. **電源落ち診断強化**（ログ追加 + 長時間充電検知）
3. **口パク大幅改善**（lipSync ロジック + 更新レート）
4. **TapDetect のなでなで化 + 誤反応対策**（最重要 UX）
5. **常時マイク**（VAD 段階導入）
6. サーバー負荷対策（並行）

---

**このレビューは `/doc/code-review-stacchan-ex-2026-07-02.md` に保存しました。**

後日実装時にこのファイルを参照してください。追加で特定のファイルの詳細 diff が必要なら教えてください。