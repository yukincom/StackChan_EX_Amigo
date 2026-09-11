# FW-005: カメラ・歌・検索後の入力モード復帰

2026-09-11。本体と公開serverへ実装し、家庭用serverともwire v2および復旧方針をそろえた。初期実装時はFlashと実運用反映を別作業としていたが、その後 `04f3b71` をCoreS3へFlashし、家庭用serverへv2とSilero VADを反映して短い実会話を確認した。現在の利用方法は [入力モード（ドラフト）](../docs/input-modes.md) を参照。

## 入力モードの保持

選択済みの「タッチ／いつでも」と、一回の会話・撮影・再生の進行状態を分ける。エラー処理からタッチを選び直さない。英語モードのON/OFFはサーバー側の従来動作を維持する。

| 処理 | タッチ | いつでも |
| --- | --- | --- |
| 通常会話・検索 | 従来chat/function-call経路の終了後、次のタッチ待ち | reply音声の再生結果をACK後、次の録音 |
| カメラ | 撮影→相関付き結果音声→action完了後、次のタッチ待ち | 同じ処理のaction完了後、次の録音 |
| 歌 | 歌本体の再生・Speaker排出後、次のタッチ待ち | 歌本体の再生・Speaker排出後、次の録音 |

- 撮影/upload失敗・8秒のカメラ結果待機期限・相関付き画像結果の`action_status=failed`では、グルグル目とSD `/stack_sd_audio/vision_error.mp3` の「あれ？うまく見れなかったみたい！」で通知し、表情を解除する。専用音声が欠ける/checked再生に失敗すると既存`error`へ一度フォールバックする。両方なくても表情解除と失敗ACKを行う。
- 明示的な画像失敗はサーバーの失敗音声を重ねず、SD音声だけを使う。画像は成功した後の結果音声取得/再生失敗や歌の再生失敗では既存`error`、接続エラーは既存`server_error`を維持する。SD上の実在・聞こえ方は実機で別途確認する。
- 成功を装った完了は送らない。成功・失敗の両方を通知し、サーバーがその完了を受理してから次の入力へ進む。通信断中も選択モードを保持し、完了通知のみを再送する。音声や撮影をやり直さない。
- 検索等の一時的なHTTP失敗・処理中409では同一body・同一event IDで再試行し、サーバーの重複排除を使用する。新しい発話を重ねず、回復後に結果を一回だけ扱う。タッチの通常chatは従来どおり終了してタッチ待ちへ戻り、旧検索要求を自動で繰り返さない。
- 不正応答・ID不一致等の継続不能な状態では、旧sessionへ`stop`を一度試みてローカル状態を破棄する。「いつでも」は新しいIDで`start`し、旧発話・旧actionを繰り返さず次の新規録音へ戻る。「タッチ」は次のタッチ待ちへ戻る。接続や認証設定が回復するまで会話の成功は保証できない。
- ユーザーの明示的なタッチ選択・アプリ退出では、自動録音と未送信/再送待ちの処理を停止する。アプリへ戻っただけでは勝手に「いつでも」を再開しない。選択のNVS/SD保存は追加していない。

## 合意済みwire v2

従来の`POST /v1/conversation/events`で`schema_version=2`を使用する。v1はSessionクラスの既定として互換テストを維持し、本体の新しい入力経路は明示的にv2を選ぶ。

- 共通値は`schema_version`, `session_id`, `event_id`, `type`。
- `start`, `speech(text)`, `face_touch`, `stop`は既存のまま。v2の`playback_completed`は`reply_id`と`status: succeeded|failed`を送る。
- `action_completed`を追加し、`action_id`と同じ`status`を送る。
- responseには共通ID、`ok`, `state`, `action`, `reply`, `device_action`を含める。`reply`と`perform`を排他的に扱い、通常replyでは`device_action=null`、performでは`reply=null`。従来の内部VISIONマーカーは発声しない。
- camera actionは`action_id`, `type=camera_capture`, `camera_mode`, `camera_requester`, `transcript`, `speaker`, `speaker_label`, 任意`announce_endpoint`。audio actionは`action_id`, `type=audio_playback`, `audio_url`。
- IDは64 Unicode文字以内、空・前後空白・CR/LF/NULを拒否する。transcriptは1000文字、speakerは64文字、speaker_labelは128文字。endpointは空/省略を許可し、指定時は64文字以内のASCII英数字・`_`・`-`のみ。URLは2048文字以内のHTTP(S)、userinfoとCR/LF/NULを拒否する。これらはwire境界であり、モデル設定の変更ではない。
- `event_processing`/`speech_processing`、非正HTTP結果、408/429/5xxは同一要求再送。`session_not_started`/`session_closed`、その他の競合、不正応答は旧結果を捨てて上記の新規session復旧へ進む。`stop`自身は自動再試行しない。

タッチでは、既存chat/function-callの各リクエストに新しい`stackchan_device:{action_schema_version:2,session_id,event_id}`を付ける。action応答の同名envelope・ID・型を検証して一回限りのactionとして採用する。通常の返答・function callは従来経路を維持し、action自体を返答テキストや本体chat履歴へ追加しない。one-shotのaction完了は`closed/stopped`、いつでもの完了は`active/listen`となる。

## 既存処理との接続

- 撮影・アップロードは既存`camera_capture_post_upload`、音声は既存`process_play_request`とMP3デコーダーを使用する。別の録音・撮影・再生ループやバックグラウンドタスクは追加しない。
- `/vision/upload`の既存queryへ`session_id`と`action_id`を両方付け、結果の`/play`または`/play_local`にも同じIDと`action_status`を要求する。現actionと異なるID、期限後・終了後の音声を409で拒否する。キュー内の同じ音声・statusの重複は200を返すだけで再登録しない。200はキュー受理であり、再生完了ではない。
- 相関付きaction中は無相関push・別の`/chat`・`/speech`・camera起動を拒否する。従来の無相関pushはactionのない時には残す。
- main loopはタッチ操作と既存HTTP/撮影/再生キューを処理した後、自動録音へ進む。撮影・音声の実行中や完了ACK再送中は、そのactionに次の録音が割り込まない。
- 歌URLはサーバー側の既存MP3 proxyを使う。従来の即時push＋開始台詞だけの完了通知を、歌本体を一つのactionとして完了通知する形に変える。既存pushのファイルサイズ/ダウンロード期限は変更していない。

## 再生完了の検証境界

MP3の受信済みbyte数を、sourceをcloseする前に有限の予告サイズと照合する。実デコーダーの自然EOF、実音声フレーム、ハードエラーなし、PCM送出成功、Speakerキュー排出を合わせて判定する。正常な先頭ID3タグ由来のsync探索と、実音声のない偽の終了を分ける。

これはソフトウェア上の再生完了であり、実機I2S・スピーカーの音量・人に聞こえたことの証明ではない。任意の末尾タグや破損MP3を全形式サポートしたという意味でもない。通常の歌は既存MP3変換で生成する。

既存の3.75秒の順次録音と同期HTTP処理は維持する。無欠落の常時収録、再生中の割込み発話、AEC full-duplexは未実装。カメラ結果の8秒待機が実際の処理時間に十分かどうかは実機で確認する。

サーバーのmemory/forecast更新時点、quiet/周辺会話判断、TTSの全文読み上げ保証は別の境界で、FW-005では変更しない。

## 検証と復旧

- v1/v2の実Sessionをホストで実行し、正常reply/camera/song、成功/失敗ACK、同一ID再試行、typed409、不正相関、文字数/URL/endpoint境界、Touch one-shotを検査する。
- 実ESP8266Audio/libmadデコーダーと本体のMP3完了ループを実行し、正常ID3/raw、空・タグのみ・破損・途中切断・PCM拒否・排出失敗・close後のサイズ消失を検査する。
- 全体回帰139件成功（15.00秒）。実`WebAPI.cpp`とSessionを直接結合した14ケースで、相関拒否、キュー重複、撮影/再生一回、ACKのみ再送、timeout一回、失敗・明示停止・Touch復帰を確認した。MP3/Camera/エラー音声helperはこの結合テストでは境界スタブであり、SDの実体や音響確認ではない。
- 実Session v2と実WebAPI結合テストをASan/UBSan付きでも実行し、成功した。選択boolを含むAiStackChan呼出側は現時点ではソースレビューと機種buildで検証している。
- CoreS3/Core2/AtomS3Rの最終buildすべて成功（32.098秒）。CoreS3 `firmware.bin` SHA-256: `21bf9ca0cb246af97dfceec3c087854686e3f2b1b77ef6d58122877cc0819557`。既存依存の非推奨警告等は残る。
- 追加の隔離診断では、実C++ Sessionと公開版・家庭用それぞれの実Python ConversationEventStoreをJSONのプロセス間入出力で接続した。結果/ACKを一度失わせ、同一イベントの再送、検索・カメラ・歌のbuilderが各一回、成功/失敗status、Touchのclosedを両版で確認した。HTTPサーバー起動・モデル実行・実ネットワークを含まないwire整合検査である。
- `04f3b71`をCoreS3へFlashし、v2対応の家庭用serverで短い実会話を確認した。STT、会話、TTS、`playback_completed`は実際の一周で成功した。本体・公開server統合後の全自動回帰は285件成功、2件skip。
- カメラ・歌・検索を組み合わせた実機E2E、長時間連続運転、部屋条件の異なる音響、物理SD上の`vision_error.mp3`は未確認。短い実会話の成功をこれらの代用にはしない。
- この作業開始時に実機へ入っていたソースは`0173011`。先行のMP3修正checkpointは`1304d3b`。両方とも復旧用のソース基点として残し、既存の非公開実機バックアップは変更しない。

## 画像失敗用SD音声の追加（同日追記）

- ユーザー指定文「あれ？うまく見れなかったみたい！」を`Copy-to-SD/stack_sd_audio/vision_error.mp3`へ同梱した。既存設定と同じ話者ID 2、ローカルVOICEVOX 0.25.1で生成し、既存の変換設定でMP3にした。24 kHz mono、128 kbps、約2.57秒、42,669 bytes。音声生成: VOICEVOX:ずんだもん。
- MP3 SHA-256: `12719a53c1e3f8aa3b59bfd2c70b7b29b196187fb3de6f173d55371c59b6d067`。実libmadと本体の完了判定でデコード・排出成功を確認する回帰を追加した。
- 追加後も全139件成功（15.11秒）。実WebAPI結合は16ケースとなり、画像失敗のSD選択、成功画像の音声配送失敗との区別、Touch復帰、ACK再送時の非再生を確認した。16ケースはASan/UBSanでも成功した。
- 実`playLocalPrompt`/`playInputErrorPrompt`の関数本体をそのままホストでコンパイルした追加診断で、専用成功、専用失敗から汎用へ一回フォールバック、両方失敗時の表情解除、汎用/接続エラー既定をASan/UBSan付きで確認した。SD/音響は境界スタブであり実機試験ではない。
- CoreS3/Core2/AtomS3R build成功（38.626秒）。CoreS3 bin SHA-256: `367987931889f3d2005f81f059736ee0aa606f8ff0ce8d2a8f8654e244c220f2`。
- FlashはSDへ音声ファイルをコピーしない。本体FTPもSPIFFS専用のため、音声は物理SDのrootから見て`/stack_sd_audio/vision_error.mp3`へ別途コピーする。既存の設定・音声は上書き/初期化しない。
