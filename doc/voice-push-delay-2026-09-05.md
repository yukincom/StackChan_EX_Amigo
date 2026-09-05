# 音声pushの固定待ちの確認

## 適用範囲

`push_to_m5stack(..., fire_and_forget=True)` で新しく受け付ける音声URL送信の固定1.5秒を廃止する。公開側の新規journalには `delay_ms=0` を記録し、家庭用実験側はスレッド内の `time.sleep(1.5)` を除く。非同期実行自体、HTTP timeout=30、重複抑制、失敗/不明状態の扱いは維持する。新しいAPIや本体改修は追加しない。

公開側で既に永続化した旧requestのdelay_msは互換性のため読めるままにする。既存受付済みデータを変更したり消去したりしない。再試行backoffの一括削除ではない。

通常の `/v1/chat/completions` は `process_chat(generate_voice_flag=False)` で文章を返し、本体のOpenAITTSが `/v1/audio/speech` を取得する。この通常経路に今回の1.5秒待ちはない。通知、定時、起動、`/chat` の音声生成指定などの非同期pushとは区別する。同期のカメラ結果pushは以前から待ち0だった。

**約7秒かかるという体感の原因・短縮量は未計測。今回の削除で通常会話が1.5秒速くなるとは言えない。**

## 上流照合

2026-09-05に上流mainを `6c82c205e16659d080e83f9dff19ef6c3a7aceef` に固定して確認。完全treeにPythonサーバーもvoice_service.pyもなく、[WebAPIの登録部](https://github.com/ronron-gh/AI_StackChan_Ex/blob/6c82c205e16659d080e83f9dff19ef6c3a7aceef/firmware/src/WebAPI.cpp#L565)にも `/play` はない。上流の `/speech` はテキストを本体側で読み上げる別経路である。

公開側の[初回記録3c62326](https://github.com/yukincom/StackChan_EX_Amigo/blob/3c62326c702bf78c30f060a40f45732f38b35cfe/server/services/voice_service.py#L150)には既に1.5秒待ちがある。初回以前の導入理由や作成者を推測で断定しない。ローカルcommit `9225ef5e` は既存の待ちをjournal/workerへ移したもので、待ちの初導入ではない。

したがって「現在の上流にも同じ送信経路で最低1.5秒待つ仕様がある」という根拠は確認できない。数字が同じだけの別のdelayを根拠にしない。

## 受信可能条件と限界

本体の `/play` はmainループがHTTP受付へ戻ったとき処理され、保留/再生中なら409、受付成功なら200を返す。200は再生完了ではない。非同期pushは元のチャットHTTP応答の完了を待つ同期呼出しではないため、送信側がその応答を止めて本体と循環待ちになる経路ではない。

固定sleepは録音・生成・再生の終了確認をしておらず、1.5秒待っても受付可能を保証しない。削除後もネットワーク要求は既存timeoutの範囲で待つ。長時間busy時の409やtimeoutを解決する変更ではなく、公開側は不確実な送信を勝手に再送しない既存方針を維持する。

テストは新規非同期配送にsleepがないこと、enqueue/スレッド分離、HTTP timeout、旧journal互換、重複抑制、不確実な送信、同期失敗をモックで確認する。実機競合・転送速度・音声遅延の保証ではない。実運用フォルダの変更、再起動、デプロイ、Flashは行わない。
