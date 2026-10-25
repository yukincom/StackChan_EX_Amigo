# ネイティブ依存の終了時エラー修復（2026-10-25）

## 再現条件と原因

前の検証で残したSIGBUSは、会話・音声を別processで試すだけでは解決していませんでした。元の混在条件を一時processで再現し、さらに次まで絞りました。

- SentencePiece 0.2.1単体、音声単体: 正常終了。
- SentencePiece 0.2.1とspaCyの英語pipeline初期化: 本文の処理が終わった後、終了時にSIGBUS。
- SentencePiece 0.2.1とcurated-tokenizers 0.0.9のimportだけ: 同じSIGBUS。MLX、音声モデル、ネットワーク、アプリ起動は不要でした。
- macOSレポートの停止箇所はSentencePiece内の `absl::Flag<bool>::~Flag()`。旧SentencePieceとcurated-tokenizers内の両native moduleが `FLAGS_version` と同名のFlag関連symbolを公開していました。同居時のC++終了処理の衝突と整合する結果です。

curated-tokenizersは英語解析側が読み込む既存依存です。2つのtokenizerを同時に使う条件を残し、SentencePieceだけを公式0.2.2へ変更すると、最小再現・spaCy・実音声・元の混在検査すべてが正常終了しました。再現不能なまま「別processだから問題なし」とは扱っていません。

[SentencePiece 0.2.2の公式リリース](https://github.com/google/sentencepiece/releases/tag/v0.2.2)にはPython bindingと内部C++実装の更新が含まれます。ただし、公式がこの特定の組合せを修正対象と明記している、という主張ではありません。解消の根拠は同じMac・同じ他依存による更新前後の比較です。

## 修正

- MLX extraに `sentencepiece>=0.2.2` を明記。以前からlockにあった0.2.2を最低版として保証します。
- 運用venvはSentencePieceだけを0.2.1から0.2.2へ更新。195 distributionsを維持し、削除なし、他packageの版変更なし。グローバルPythonの0.2.1や他プロジェクトは変更しません。
- test groupへSentencePieceとcurated-tokenizersを追加し、fixtureやモデル取得なしで同居・変換・終了を確認します。運用lockのcurated-tokenizers 0.0.10を含む既存の固定版は維持しました。既存運用の0.0.9との互換性も別途確認しています。
- 子process内で日英混在文字列を両tokenizerで同じIDへ変換・復元し、読込順序2通りを確認。成功markerだけでなく子processの終了code 0も必須にしました。旧環境ではmarker後のSIGBUSにより実際に失敗することを確認済みです。
- 既存workflowへmacOS arm64の回帰jobを追加。Linuxの全体試験も維持し、トリガーはpushだけ。定期・PR・手動トリガーは追加しません。macOS jobでは音声/MLX extra・モデルを導入せず、tokenizerだけを試します。

終了処理の強制打切り、例外の握り潰し、import順の回避、機能削除、UI追加、別サービス・API route・起動入口の追加はしていません。Python 3.11.14、共有モデル、VOICEVOX、本体ファームは維持します。

## 検証

- 更新前: 元の混在検査と最小再現が `returncode=-10`、処理完了markerありで失敗。
- 更新後: 最小再現・読込順序2通り・日英変換と終了の回帰試験が成功。
- 音声単体・SentencePiece+音声・MLX等8 packageの混在検査: 実Kokoro推論を含め、正常終了。
- 公開版/家庭用の実処理を使った混在検査: 既存ローカル8080の会話・要約、実VOICEVOX/Kokoroの日英WAV、既存cached VLMで合成画像の説明まで実行し、終了code 0。実機へ新しく切り替えた試験とは区別します。
- 全体試験: 公開版 **360 passed**、家庭用 **238 passed**、skipなし。実機・常駐serviceへの接続禁止は維持。
- CI相当の独立venvをlockから作り、個人設定・音声/画像モデルなしで回帰試験3件が成功。

合成画像は一時的な赤い正方形だけで、個人の写真・会話・認証情報を公開側へコピーしていません。既存アプリの起動手順・見た目は変えず、稼働環境への反映は同じGUIから行います。push後のGitHub実行結果は実際の対象SHAで確認し、ローカル試験をCI結果として扱いません。
