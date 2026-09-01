# Python 依存とライセンス

`pyproject.toml` / `uv.lock` に固定した直接依存のライセンス確認表です。2026-09-01 時点で、固定 version の installed distribution metadata と各 upstream の LICENSE を照合しました。間接依存の完全な SBOM ではありません。

## Core / test

| package | version | license | 用途 |
| --- | ---: | --- | --- |
| APScheduler | 3.11.2 | MIT | 既存 scheduler |
| Flask | 3.1.3 | BSD-3-Clause | 既存 server と仮想 HTTP service |
| Werkzeug | 3.1.8 | BSD-3-Clause | 既存 server、loopback test server |
| google-genai | 1.72.0 | Apache-2.0 | 既存 cloud provider adapter |
| huggingface-hub | 1.12.0 | Apache-2.0 | 既存 model取得 adapter |
| jpholiday | 1.0.3 | MIT | 既存 scheduler |
| openai | 2.31.0 | Apache-2.0 | 既存 OpenAI-compatible adapter |
| python-dotenv | 1.2.2 | BSD-3-Clause | 既存設定読込 |
| requests | 2.33.1 | Apache-2.0 | 既存 HTTP client、仮想契約 client |
| pytest | 9.0.3 | MIT | 新規 test runner |

ラボ専用の emulator framework は追加していません。標準 library と既存の Flask/requests を再利用しています。

`uv 0.10.2` は環境同期/lock 用の開発 toolで、runtime dependency ではありません。upstream は Apache-2.0 と MIT の dual license です。

- [pytest 9.0.3 LICENSE](https://github.com/pytest-dev/pytest/blob/9.0.3/LICENSE)
- [uv 0.10.2 LICENSE-APACHE](https://github.com/astral-sh/uv/blob/0.10.2/LICENSE-APACHE)
- [uv 0.10.2 LICENSE-MIT](https://github.com/astral-sh/uv/blob/0.10.2/LICENSE-MIT)

## Optional extras

CI と実機なし core test は次を install しません。

| extra | package/version | metadata 上の license |
| --- | --- | --- |
| `voice` | kokoro 0.9.4 | Apache-2.0 |
| `voice` | numpy 2.4.4 | BSD-3-Clause、0BSD、MIT、Zlib、CC0-1.0 の複合表記 |
| `voice` | soundfile 0.13.1 | BSD-3-Clause |
| `mlx` | mlx-lm 0.31.2 | MIT |
| `mlx` | mlx-vlm 0.4.4 | MIT |

モデル weight、VOICEVOX、whisper.cpp、ffmpeg、PlatformIO library はこの表の対象外です。モデルは code license と別条件の場合があるため、追加/更新する時点で個別に確認します。
