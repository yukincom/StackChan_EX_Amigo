# 本体なしのテキスト対話ラボ

## 何を試せるか

公開版・家庭用の共通 `services/conversation_text_lab.py` を使い、発話テキストから場面を推定して既存の参加reducerへ渡す。`app.py`、設定ファイル、通常の記憶、スケジューラ、STT、TTS、本体APIは読み込まない。実運用の会話経路には未接続。

- ファイル再生: 1発話ずつ「待つ / 返答する / 静聴を告げる / 聞き始める」と参加状態を比較し、JSONと閲覧用HTMLを作る。
- 自由入力: テキストと `/touch`、`/gap`、`/off`、`/on` を入力する。終了は `/quit`。入力をファイルに保存しない。
- これは **応答タイミング・参加判断の評価**。通常は返答本文を生成しない。任意の比較デモだけでQwenの返答文を生成できるが、返答内容の品質、音響上の無音/割込み時間、複数人の話者識別、実機の再生停止を検証したことにはならない。
- `local` は明示指定した既存ローカルLLMを使う。外部APIキー・プロキシ・リダイレクトは使わない。モデルのインストール、プロセスの再起動、実運用設定の変更はしない。指定モデルは事前に稼働中のものと一致させる。
- `oracle` は正解注釈を場面に変換する **配線テスト**。テキストの意味理解ができた証拠ではない。`always_silent` / `always_reply` は比較用の退化ベースライン。

## 長さと保持量

1発話 = 一人の発話単位。ただしLive文字起こしは複数人が一つのメッセージになるため、実データの発話分割は推定として記録する。1往復（人→AI）とは数え方が異なる。顔タッチや区切り操作は発話数に含めない。

**短い抜粋12〜20発話、保持・再遷移確認30〜50発話**を今回の初期設計とする。下記研究がこの数字を最適値として実証したわけではない。元会話ごとに開発用と保留評価用を分け、同じ会話の近接抜粋や派生を両側へ分散させない。

| 一次資料 | 確認内容 | 設計への反映 |
| --- | --- | --- |
| [MultiChallenge](https://arxiv.org/html/2501.17399v2), 表1・§1 | 273会話、平均5 turns、最大10-turnの会話履歴。 | 短い区間にも記憶・文脈の課題を入れる。家庭内参加判断の最適長を示す研究ではない。 |
| [MT-Eval](https://aclanthology.org/2024.emnlp-main.1124.pdf), 表10・§4.6 | 課題別の平均長3/7/10/12 turns。無関係な文脈の挿入と誤りの伝播を評価。 | 全長だけでなく同じケースの保持量を0/4/8/16/32発話で比較。 |
| [Bohus & Horvitz, 2011](https://aclanthology.org/W11-2013.pdf) | 発話者、宛先、発言権を分ける多人数対話の研究。視線なども利用。 | 人同士への誤割込みとAIへの呼びかけの聞き逃しを分ける。テキストのみで同等性能とは主張しない。 |
| [OpenAIの評価指針](https://developers.openai.com/api/docs/guides/evaluation-best-practices) | 用途に即したデータ・採点と、人間の評価との照合を推奨。 | 元GPTの返答を自動で正解にせず、観測と暫定注釈を分ける。 |

通常履歴の `AI_RECENT_TURNS` はuser/assistantの組を数える。コード既定値は5往復。検索時の `AI_SEARCH_RECENT_TURNS=1` は今回入力だけという別設定。これを一括で増やしても静聴中の周辺会話は保持できない。

新しい `TextContext` は **直前16発話＋今回入力、合計12,000文字以内**を初期値とする。通常履歴ではなく、静聴中の人間同士の発話も含む一時的な場面文脈。古い発話を丸ごと落とし、現在入力が文字上限を超える場合は黙って切らずエラーにする。単位は文字でありトークン数ではない。モデルが返す使用トークン数も別に記録する。

参加状態は文脈から独立して保持し、古い発話が落ちただけで勝手に復帰しない。完全マイクOFF操作では短期文脈を消し、その間は推論・文脈追加を行わない。固定ケースの本文は私的レポートには掲載されるので、これはテスト素材自体の消去機能ではない。

**通常履歴・検索履歴・実運用の設定ファイルは今回変更しない。** 場面判定用の保持量だけを比較可能にした。既存の往復数と新しい発話数を単純比較して「増やした」とは扱わない。

## 実行

リポジトリ直下で実行する。実データと結果のパスは非公開の保存先を指定する。

```sh
.venv/bin/python tools/conversation_lab.py --cases /private/path/live_cases.json --backend oracle --split all --history 4,8,16,32 --output /private/path/results/new-run
.venv/bin/python tools/conversation_lab.py --cases /private/path/live_cases.json --backend local --model ALREADY_RUNNING_MODEL --split dev --history 0,4,8,16,32 --output /private/path/results/new-model-run
.venv/bin/python tools/conversation_lab.py --interactive --backend local --model ALREADY_RUNNING_MODEL --name スタックちゃん
```

返答文も比較する場合は、localの1ケース・保持幅1つを指定して `--demo-turn u04 --demo-turn u16` のように対象IDを追加する。ケースに明示した名前と人格を使う**簡易生成器**であり、本番の `ai_handler.get_ai_response` や `process_chat` は通らない。同じQwenが生成した文を、参加判断なし条件では表示し、参加判断あり条件では `reply` のときだけ表示する。生成文の違いを混ぜず「返すかどうか」の効果を見せる制御比較であり、実運用の過去出力・本番パイプラインの改善結果と呼ばない。

### キャラクター名の整合性

本番設定を読み込まないため、各ケースの `assistant_names` が登録名の全て。Qwenというモデル名やGPTというサービス名を勝手にキャラクター名として追加しない。自由入力にも `--name` が必須であり、既定名への暗黙フォールバックはない。ファイル再生時の `--name` は無視せずエラーにする。

返答デモは同じ `assistant_names` と明示的な `assistant_persona` を生成器へ渡す。人格未設定のデモは推論前に停止する。レポートに登録名を表示し、GPTを登録した原ログ再現と、家庭用/公開版の名前による派生試験を混ぜない。原文の呼び名を置換した場合は派生データであることと変換内容を残す。

合成の誤名試験も併用する。例: 登録名が「サクラ」のとき「GPT、返事して」には復帰しない。「サクラ、返事して」には復帰する。名前が話題に出ただけの発話も別に試す。名前を設定せずに汎用Qwenが返事したことを製品の成功と数えない。

家庭用実験は `--server-root /path/to/yuno-chan-api-conversation` を同じランチャーに追加する。家庭用実運用フォルダは指定しない。レポートがすでに存在する場合や保持幅が不正な場合は、推論前に止まる。

再生のexit 0は採点対象がすべて注釈に一致、exit 1は不一致/判定エラーを含む。判定が失敗したときの「待つ」は正解の沈黙として数えない。HTMLでは行の期待actionとmodeを両方比較し、状態だけの不一致も失敗表示する。

JSONは `schema_version: 1` と `cases` 配列。各caseには `id`, `split`, `source_group`, `assistant_names`, `turns` が必要。発話は `id`, `role: human|assistant|unknown`, `text`。操作は `kind: face_touch|turn_boundary|mic_off|mic_on`。任意の `playback_active` は合成の再生状態として扱う。

`gold` は採点専用。`action`、任意の `mode`、カテゴリ、oracle用の `scene` を持つ。必要なら `stop_playback` と `allow_conversation_history` も採点する。正解宛先・話者の推測・出典・未来の発話はlocal判定器へ渡さない。原ログのassistant発話は過去文脈として渡すため、これは記録文脈リプレイでありモデル自身の応答による自由会話評価ではない。

## 私的データとOSS

GPT Live由来の原文、匿名化抜粋、出典ID、暫定ラベル、評価結果は公開Gitへ追加しない。匿名化しても私的会話である。自動学習・外部送信・デプロイはしない。顔タッチや無音のタイミングが原ログにない場合は合成操作として別扱いにする。テキスト応答がないことから音声の沈黙理由やパーソナライズの内部処理を推定しない。

汎用の単体テストは既存の[pytest（MIT）](https://github.com/pytest-dev/pytest/blob/main/LICENSE)を使用。汎用評価基盤を新規に自作せず、ここには当プロジェクトのreducerとテキストデータを結ぶアダプターだけを置く。追加依存なし。

将来の複数モデル/プロンプト比較基盤には[Inspect AI](https://inspect.aisi.org.uk/solvers.html)の導入を提案する。[MIT原文](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/LICENSE)を確認済み。今回は導入せず、既存pytestと小さなケースで基準を固める。クラウドの評価サービスや有料APIを契約する必要はない。
