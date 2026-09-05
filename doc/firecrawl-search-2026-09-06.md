# Gemini / Firecrawl検索の選択

## 設定

管理UIの「AI設定」で検索サービスを `Gemini / Google検索` または `Firecrawl` から選び、APIキー欄へ対応するキーを入力して保存する。
既存の設定方式どおり、サーバー再起動後に反映する。本体Flashは不要。

- `AI_SEARCH_PROVIDER`: `gemini`（既定）または `firecrawl`。
- `FIRECRAWL_API_KEY`: Firecrawl利用時のキー。Hermesから自動コピーしない。
- `AI_SEARCH_MODEL` / `AI_SEARCH_TEMPERATURE`: Gemini検索用。Firecrawl利用時の回答は既存の会話プロバイダー・会話モデル・会話temperatureを使う。
- APIキーは既存の `.env.local` 保存方式を利用する（暗号化保管ではない）。GET応答に値を返さず、空欄保存は既存キー維持。キーを公開リポジトリへ入れない。
- UIの保存だけでは稼働中プロセスへ即時反映されない。未設定キー・認証/残量/通信エラーでGeminiへ自動切替しない。

## 検索の範囲

検索トリガーは従来どおり。通常会話ではFirecrawlを呼ばない。
Firecrawlへ送るqueryは既存の名前マスクを通した今回の発言のみ。会話履歴・ペルソナ全体は検索APIへ送らない。
指示語だけの「それを調べて」などは検索語として不十分になり得る。履歴からの自動query生成は今回追加していない。

公式 `POST https://api.firecrawl.dev/v2/search` を既存requestsで呼ぶ。web検索のタイトル・URL・descriptionを取得し、会話モデルへ参考資料として渡す。
検索結果内の指示を実行しないよう明示するが、これはLLMの誤判断を完全に防ぐ保証ではない。
本文scrape・crawl・ブラウザ操作・複数回検索・自動再試行は追加しない。要約断片だけで答えられない場合は不明として扱う。

Firecrawlは外部のクレジット消費サービス。同じキーをHermesと手動で共有した場合は同じ枠を使う。契約/残量/自動課金設定は今回確認・変更していない。
実キーで検索しておらず、料金が発生する疎通テストもしていない。

## 依存・検証

- 新規SDK/依存の導入なし。既存requests 2.33.1はApache-2.0（インストール済み配布メタデータで確認）。HermesやFirecrawlサーバーのコードはコピーしていない。
- 公開版: 全131件成功（localhost許可後）。最初のsandbox実行はbind制限による12失敗/119成功で、製品不具合とは区別。
- 家庭用の分離worktree: unitテスト80件成功。両版の検索/UI API専用テストは各15件。
- Gemini/Firecrawlの振分け、queryと回答promptの分離、認証/残量/制限/通信/不正応答、空結果、キーの非表示・保存・空欄維持、不正設定を確認。
- UIは既存のselect/password描画に項目定義を追加。実ブラウザでの見た目、実キー検索、稼働サーバー再起動は未実施。
- 本体コード変更/Flash/実運用反映なし。家庭用の実運用dirtyフォルダは変更しない。

参照: [Search API](https://docs.firecrawl.dev/api-reference/endpoint/search)、[料金](https://www.firecrawl.dev/pricing)。
