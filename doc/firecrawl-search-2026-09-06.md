# Gemini / Firecrawl検索の選択

## 設定

管理UIの「AI設定」で検索サービスを `Gemini / Google検索` または `Firecrawl` から選び、APIキー欄へ対応するキーを入力して保存する。
既存の設定方式どおり、サーバー再起動後に反映する。本体Flashは不要。

- `AI_SEARCH_PROVIDER`: `gemini`（既定）または `firecrawl`。
- `FIRECRAWL_API_KEY`: Firecrawl利用時のキー。Hermesから自動コピーしない。
- `AI_SEARCH_RECENT_TURNS`: 検索回答とFirecrawl検索語の生成に使う総ターン数。既定は今回を含む3ターン（直前2往復まで）。
- `AI_SEARCH_MODEL` / `AI_SEARCH_TEMPERATURE`: Gemini検索用。Firecrawl利用時の回答は既存の会話プロバイダー・会話モデル・会話temperatureを使う。
- APIキーは既存の `.env.local` 保存方式を利用する（暗号化保管ではない）。GET応答に値を返さず、空欄保存は既存キー維持。キーを公開リポジトリへ入れない。
- UIの保存だけでは稼働中プロセスへ即時反映されない。未設定キー・認証/残量/通信エラーでGeminiへ自動切替しない。

## 検索の範囲

検索トリガーは従来どおり。通常会話ではFirecrawlを呼ばない。
Firecrawlを選んだ場合は、既存の会話モデルが名前マスク済みの今回の発言と直前2往復以内を読み、検索専用のqueryを1件だけJSON生成する。「それを調べて」のような指示語は、この段階で直前の話題を補う。
生成結果は一行・200文字以内・単一fieldという閉じた形式で検証する。対象を特定できない、形式が不正、個人を示すマスクラベルが残る場合は、Firecrawlを呼ばず検索を中止する。生の発言へ自動的に戻さない。

検索語の生成は、Hermesの完全なtool loopではなく、明示的な検索要求の内側だけを会話モデルへ任せる限定的なagentic経路である。検索開始をモデル任せにしないため、意図しないクレジット消費を避けられる。
会話モデルへ渡す検索語生成資料にペルソナ・家族構成・注釈・興味は含めない。Firecrawl APIへ送るのは検証済みqueryだけで、会話履歴や人格promptは送らない。

公式 `POST https://api.firecrawl.dev/v2/search` を既存requestsで呼ぶ。web検索のタイトル・URL・descriptionを取得し、会話モデルへ参考資料として渡す。
検索結果内の指示を実行しないよう明示するが、これはLLMの誤判断を完全に防ぐ保証ではない。
本文scrape・crawl・ブラウザ操作・複数回検索・自動再試行は追加しない。検索結果を見たモデルによる追加queryも行わない。要約断片だけで答えられない場合は不明として扱う。

Firecrawlは外部のクレジット消費サービス。同じキーをHermesと手動で共有した場合は同じ枠を使う。契約/残量/自動課金設定は今回確認・変更していない。
実キーで検索しておらず、料金が発生する疎通テストもしていない。

## 依存・検証

- 新規SDK/依存の導入なし。既存requestsを使う。HermesやFirecrawlサーバーのコードはコピーしていない。
- Gemini/Firecrawlの振分け、文脈からのquery生成、queryと回答promptの分離、閉じた形式と個人マスクの検証、生成失敗時に検索しないこと、認証/残量/制限/通信/不正応答、空結果、キーの非表示・保存・空欄維持、不正設定を自動テストする。
- UIは既存のselect/password描画を使い、選択中サービスと保存・再起動待ちを表示する。
- 本体コード変更/Flashは不要。実キー検索と稼働サーバーへの反映は別途確認する。

参照: [Search API](https://docs.firecrawl.dev/api-reference/endpoint/search)、[料金](https://www.firecrawl.dev/pricing)。
