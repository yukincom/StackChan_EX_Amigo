# 開発・検証記録

`doc/` は日付付きの調査・設計・検証記録、`docs/` は現在の利用手順です。どちらも公開リポジトリに含まれます。

導入・操作・設定は [プロジェクトREADME](../README.md)、[server連携](../docs/server.md)、[入力モード](../docs/input-modes.md) を参照してください。過去の記録にあるモデル名、個別checkoutのパス、未対応項目、テスト件数は、その時点の情報です。

## 入力モード・本体

- [カメラ・歌・検索後の入力モード復帰（FW-005）](conversation-actions-2026-09-11.md): 会話event v2と、CoreS3での短い実会話の検証記録
- [入力メニュー](input-mode-menu-2026-09-11.md): 長押し操作と画面の設計
- [本体改修の段階別記録](firmware-milestones-2026-09-05.md): 撮影mode、待機顔、口パクなど
- [本体改修の全体計画](firmware-campaign-2026-09-05.md): 初期の切り分け方針
- [音声push遅延](voice-push-delay-2026-09-05.md): 当時の音声配送調査

## 会話・検索

- [次発話予測とSTT](conversation-forecast-stt-2026-09-10.md)
- [Firecrawl検索](firecrawl-search-2026-09-06.md)
- [テキスト対話ラボ](text-conversation-lab-2026-09-05.md)
- [会話参加の初期設計](conversation-participation-2026-09-05.md)
- [会話参加の研究調査](conversation-participation-research-2026-09-06.md)
- [会話構造の実験設計](conversation-structure-experiment-2026-09-06.md)

会話参加・静聴のreducerとテキストラボはオフライン評価用です。`いつでも`入力の実運用経路で、周囲の人同士の会話を判別して静聴する機能とは別です。

## 過去のレビュー・計画

- [C++保守レビュー（8月13日）](cpp-maintenance-review-2026-08-13.md)
- [次段階の候補（7月13日）](next-steps-2026-07-13.md)
- [上位5件の修正計画（7月11日）](pr-plan-top5-bugfix-2026-07-11.md)
- [コードレビュー（7月11日）](code-review-stacchan-ex-2026-07-11.md)
- [改修計画レビュー（7月2日）](code-review-stacchan-ex-2026-07-02.md)

レビュー中の未チェック項目は現在の未解決一覧ではありません。たとえばConfigUI認証、会話ログとMLX生成のロック、口パク更新周期、HTTPの2xx判定には後続実装があります。過去の提案コードをそのまま適用せず、現在のソースと照合してください。
