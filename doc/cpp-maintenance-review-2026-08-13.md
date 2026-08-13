# C++ firmware メンテナンス再レビュー（2026-08-13）

対象は `firmware/` の現行コードと、旧レビュー
`code-review-stacchan-ex-2026-07-11.md`。上流は
`ronron-gh/AI_StackChan_Ex` の `main`、`6c82c20`（v0.23.1、2026-08-10）までを照合した。

## 今回の結論

ビルドだけで確認でき、Amigo の構成を崩しにくい不具合修正を先に適用した。
Realtime/Gemini/Web設定など、Amigo に存在しない上流機能は取り込んでいない。
ファームウェア版表記も Amigo 独自のため上流値へ変更していない。

### 適用した修正

- MCP 設定数を固定配列上限10件でクランプし、disabled・接続失敗・OOM時のNULL参照を防止した。
- MCP SSE応答待ちに15秒、connect/HTTP処理に5秒の上限を追加し、JSON不正時は接続失敗、JSON-RPC errorは即時完了として扱うようにした。
- Whisper録音領域とmultipart送信領域のOOMを検出し、multipart送信領域はPSRAM優先・通常RAMフォールバックにした。Mic APIの失敗と最終chunkの完了待ちも検証する。
- MP3 decoder/bufferのOOMを非致命化し、PSRAMを優先した。
- push MP3に1MiB累計上限、5秒idle timeout、30秒の転送deadline、書込み失敗・空body・途中切断の検証と一時ファイル削除を追加した。
- timer/timer_changeのNULL・範囲・FreeRTOS API失敗を検証した。callbackは期限通知だけを排他的に移譲し、main taskをtimer handleと後処理の所有者にすることで、満了とcancel/changeの競合を抑止した。power-offの音声再生とdelayもtimer service task外へ移した。
- Scheduler基底クラスにvirtual destructorを追加し、schedule indexの境界チェックを修正した。
- Status Monitorの到達不能だった3ページ目を表示対象にした。
- Wi-Fi passwordのSerial生値出力を `(set)` / `(empty)` へ置き換えた。
- camera以外の空LLM応答でcamera待機状態へ入る誤判定を除去した（旧Issue 30）。

### 上流から適用・適応した変更

- `67729ec`: `QRdisplayMod.h` の `<arduino.h>` をLinuxでも正しい `<Arduino.h>` に修正。
- `6c82c20` を再設計: AmigoではCore2/CoreS3もpush MP3等でSPIFFSを使うため、全対象で共通mountする。ただし上流の `SPIFFS.begin(true)` は一時的なmount失敗でも設定を消し得るため採用せず、通常bootは `SPIFFS.begin(false)` とした。Core2には `board_build.filesystem = spiffs` も追加した。
- `5013c4f` の安全な部分: Wi-Fi passwordをログへ出さない変更を、YAML経路とlegacy入力経路の双方へ適用した。
- `35ebf20`: CoreS3のM5Unified指定を0.1.17から0.2.15へ更新した。
- `eeb9a96` 相当: 上流のMCP件数上限処理を、Amigoの最大10件へ合わせて適用した。

## 旧レビューの再評価

| 旧Issue | 2026-08-13時点 | 判断 |
| --- | --- | --- |
| 10 push MP3 | 部分解消 | サイズ・停止・書込み・途中切断は対処。URLのhost allowlistとLAN API認証は未対応。 |
| 11 loop同期処理 | 未解消 | `/play` とcameraだけでなく `/speech`、`/chat` も同期処理。queue/task化は別変更にする。 |
| 12 Whisper OOM | 主要部解消 | NULLを検出してエラー化し、2つの大領域をPSRAM優先にした。multipartの二重保持自体は残る。 |
| 13 JSON 2KB上限 | 旧説明は陳腐化 | ArduinoJson 7.4.3の互換 `DynamicJsonDocument(size)` は固定capacity上限ではなく、文書は動的に成長する。deprecated API、heap上限方針、OOM検出は別の保守課題。 |
| 17 lipSync | 未解消 | 更新周期・RMS/envelope改善は実機UX評価と一緒に行う。 |
| 18 watchdog | 未解消 | 長い同期I/Oとのfeed設計が必要。単純な再有効化は避けた。 |
| 21 device API認証 | 未解消 | LAN tokenまたは送信元制限が必要。設定互換を伴うため別変更。 |
| 22 電源診断 | 未解消 | 充電時間・電圧・リセット理由の実機ログ設計が必要。 |
| 24 custom HTTPS | 未解消 | `setInsecure()`を既定で使う。CA設定または明示的insecure optionが必要。 |
| 25 MP3内部heap | 解消 | PSRAM優先、fallback、初期化失敗時のgraceful degradationへ変更。 |
| 30 camera待機誤判定 | 解消 | 空応答をcamera triggerとみなす処理を削除。 |

今回の追加監査では、旧レビューになかったMCP固定配列overflow、disabled MCPのNULL参照、
MCP無期限待ち、timer handleのNULL操作/満了後リークも高確度の不具合として確認し、対処した。

## 今回は保留した上流差分

- Avatar描画最適化（`c7be181`、`b47205f`、`5646a8c`）: 毎frameのPSRAM確保を減らせるが、常駐PSRAM量・顔描画・stack sizeを実機で確認してから入れる。
- ChatGPT HTTP成功判定の一般化（`106ac74`）: 2xx対応部分は有用だが、custom HTTPSの証明書方針と分離して入れる。
- SPIFFS未フォーマット時の自動format（`6c82c20` の方式）: データ消去を伴うため通常bootから分離し、明示的なrecovery操作として設計してから入れる。
- AP/Web設定、Realtime/Gemini、ESP-NOW、head touch、新status UI: Amigoの機能構成や操作仕様と一致しないため対象外。

## 検証

PlatformIO release buildを実行した。

| environment | 結果 | RAM | Flash |
| --- | --- | ---: | ---: |
| `m5stack-cores3` | SUCCESS | 75,544 / 327,680 (23.1%) | 2,340,229 / 6,553,600 (35.7%) |
| `m5stack-core2` | SUCCESS | 70,272 / 4,521,984 (1.6%) | 2,601,021 / 6,553,600 (39.7%) |
| `m5stack-atoms3r` | SUCCESS | 63,028 / 327,680 (19.2%) | 2,308,985 / 3,342,336 (69.1%) |

既知のwarningは主にArduinoJson 7のdeprecated互換APIとroot CAファイル末尾。
また、`stackchan-arduino`、`esp8266FTPServer`、`SCServo`などに未固定Git依存があり、
新規環境で解決されるcommitが変わり得る。再現可能buildのためのpinningは別途必要。

### 実機で確認する項目

1. CoreS3 / Core2 / AtomS3Rの初回起動、既存SPIFFS内容、SD設定読み込み。
2. M5Unified 0.2.15でCoreS3のcamera、touch、mic/speaker、servo。
3. Whisper録音・成功応答・サーバ停止・PSRAM圧迫時のgraceful error。
4. push MP3の通常再生、chunked応答、1MiB超過、途中切断、SPIFFS空き容量。
5. MCP enabled/disabled、接続失敗、15秒timeout、複数server/tool call。
6. alarm/shutdownの満了・変更・キャンセルを期限前後で繰り返し、cancel成功後に発火せず、timer serviceが停止しないこと。

## Grok Build利用記録

更新後の `grok-build-delegator` に従い、調査用のread-only workerを1回だけ実行した。
session JSONとevent JSON、wrapper ledgerを `jq -e` で突合し、実モデル `grok-4.6`、
`reasoning_effort = low`、`sandbox_profile = read-only`、4 loopsを確認した。一方、終了状態は
`outcome = cancelled` / `reason = max_turns_reached`、wrapperは `exit-1` で正常終了条件を満たさなかった。
このためworkerの完了主張や提案は採用せず、自動retryも行わず、親Codexとnative subagentの
調査・全diff確認・build結果だけを統合判断に使用した。
