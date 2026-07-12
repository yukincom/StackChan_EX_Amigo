# Code Review: StackChan_EX_Amigo (full project)

**Date**: 2026-07-11  
**Project root**: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo`  
**Scope**: Independent full-project review (firmware + server + Copy-to-SD). Prior review (2026-07-02) consulted only for continuity.  
**Policy**: Findings only — no source fixes applied.

## Summary

The Amigo architecture is coherent: firmware owns capture/playback/camera, Flask owns chat/STT/vision/admin, and `voice_server` owns TTS. Reliability is undermined less by missing features than by **unauthenticated admin/API surfaces**, **path traversal / SSRF**, and **lock-free shared state** on `today.md` and MLX. Prior UX concerns (lipSync ~10 Hz, watchdog disabled, power management thin) are still present. Prefer a short security + memory-concurrency fix pass on the **server** before large feature work; firmware can continue UX polish in parallel once server auth and write-path sanitization land.

## Issues

### Issue 1 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/admin_routes.py:97`
- Description: Admin UI and all `/admin/api/*` endpoints (member/env/API keys/announcements/voice_cache/MLX download) have **no authentication**. Combined with `app.run(host="0.0.0.0")` this exposes secret write/read on the LAN (and wider network if reachable).
- Suggestion: Require a shared secret / basic auth / bind to localhost by default; never serve raw API keys over unauthenticated GET.
- Status: open

### Issue 2 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/admin_routes.py:355`
- Description: `GET /admin/api/env`, `GET /admin/api/ai_env`, etc. return live env values including `DISCORD_BOT_TOKEN`, `GEMINI_API_KEY`, `OPENAI_API_KEY` via `_build_env_response` / `os.getenv` (see password-typed keys around lines 218–304 and response builder at 343–352). Any LAN client can harvest secrets.
- Suggestion: Mask secrets on GET (e.g. show only last 4 chars or a boolean “set”); require auth for POST writes; reject unknown keys in `_write_dotenv`.
- Status: open

### Issue 3 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/openai_compat_routes.py:136`
- Description: `/audio/proxy.mp3?src=` performs an unrestricted server-side `requests.get(src)` — classic **SSRF**. Firmware push paths build this URL in `voice_service.build_push_audio_url`, but the endpoint is public and accepts any `src`.
- Suggestion: Allowlist hosts (voice_server / localhost only), scheme `http` only, and optional path prefix `/voice/`. Reject private/metadata IPs if external URLs are ever needed.
- Status: open

### Issue 4 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/services/voice_cache_catalog.py:58`
- Description: `get_pc_cache_audio_path` / `get_stack_sd_audio_path` only `.strip()` the filename. Values like `../../somewhere/evil` join under the configured dir and resolve outside it. Used by admin generate (`admin_routes.py:431`) and by `voice_server` cache serve (`cache_manager.get_cache_path` → `voice_server.py:210`).
- Suggestion: Reject path separators and `..`; allow only `[A-Za-z0-9._-]`; resolve and assert `path.is_relative_to(base_dir)`.
- Status: open

### Issue 5 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/voice_server.py:223`
- Description: `/song/<song_name>` builds `SONGS_DIR/{song_name}.wav` with no sanitization (`../` traversal). `/voice/<voice_id>` similarly trusts `voice_id` for filesystem paths (line 215). Unauthenticated + `0.0.0.0:5001`.
- Suggestion: Sanitize IDs (alphanumeric/uuid only); resolve paths and enforce containment; prefer binding voice_server to `127.0.0.1` if only app.py needs it.
- Status: open

### Issue 6 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/memory_manager.py:36`
- Description: `today.md` is updated with plain `open(..., "a")` / full-file rewrites and **no process-wide lock**. Concurrent writers include: chat appends (`add_conversation`), vision threads (`vision_handler.py`), weather section rewrite (`weather_service.py:658`), and batch archive rewrite (`batch_service.py:86`). Startup runs `run_if_needed()` then immediately accepts traffic (`app.py:55–63`), so a long summary can race live conversation appends and **drop or corrupt history**.
- Suggestion: Use a single `threading.RLock` (or file lock) around all read-modify-write of `today.md`; make batch run before accepting requests or re-read+merge under the lock; prefer append-only + periodic compact.
- Status: open

### Issue 7 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/openai_compat_routes.py:155`
- Description: Primary firmware STT path is `/v1/audio/transcriptions` (see `firmware/src/stt/Whisper.cpp:22`). This endpoint ignores `is_english_mode()` and only uses form `language` (default `"ja"`). Firmware always posts `language=ja` (`Whisper.cpp:34`). Meanwhile `/speech/transcribe` does honor English mode (`app.py:148–151`). After “英語モード”, device STT still uses the Japanese Whisper model/path → wrong transcripts / dropped speech.
- Suggestion: In `transcriptions()`, call `is_english_mode()` (or accept model switch) and pass `use_english_model=True`; optionally let firmware omit hard-coded `ja` when server controls language.
- Status: open

### Issue 8 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/llm_client.py:149`
- Description: MLX model load is protected by `_mlx_loading_lock`, but `generate()` itself is not. Flask serves concurrent requests; overlapping `call()`/`call_summary()`/`chat` + vision AI can race on shared `_mlx_model` / processor and crash or produce garbage.
- Suggestion: Hold a generate lock (or single-worker queue) for the entire load+generate critical section; same pattern for VLM if concurrent vision uploads occur.
- Status: open

### Issue 9 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/app.py:181`
- Description: Every `/vision/upload` starts an unbounded `threading.Thread` daemon. Concurrent camera triggers / retries can pile up VLM + LLM + TTS work with no queue, semaphore, or backpressure — memory growth and overlapping MLX use (Issue 8).
- Suggestion: Use a single worker queue (maxsize=1 or 2) with drop-oldest/busy response; return 429 when saturated.
- Status: open

### Issue 10 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/firmware/src/driver/PlayMP3.cpp:147`
- Description: `playMP3URL` downloads the entire HTTP body to SPIFFS `/push_tmp.mp3` with **no Content-Length / size cap** and no host allowlist. `/play` on the device (`WebAPI.cpp:219`) is unauthenticated LAN API: any peer can force the CoreS3 to fetch and store large data until SPIFFS fills (hang/boot issues).
- Suggestion: Cap download size (e.g. 512KB–1MB); allowlist server host/port from config; stream with abort on excess; fail closed on missing Content-Length if needed.
- Status: open

### Issue 11 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/firmware/src/WebAPI.cpp:305`
- Description: `process_play_request` and `process_camera_trigger_request` run **synchronously in `loop()`** (called from `main.cpp:577–578`) for the full download/playback/camera upload. During that time `web_server_handle_client()` is not serviced → `/play` 409s, missed camera triggers, stalled status checks; push TTS from server can fail under load.
- Suggestion: Offload play/camera to a FreeRTOS task with a small request queue; keep `loop()` only for enqueue + `handleClient`.
- Status: open

### Issue 12 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/firmware/src/driver/AudioWhisper.cpp:12`
- Description: Constructor `heap_caps_malloc`s the full record buffer (~120KB+) but **never checks for NULL**. OOM → crash on `memset`/`Record`. Whisper then also `malloc`s a second full body copy for multipart (`Whisper.cpp:41`) on the default heap — double peak memory during STT.
- Suggestion: Null-check and surface STT error; stream multipart or send buffer without full second copy; keep large buffers in PSRAM only.
- Status: open

### Issue 13 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/firmware/src/llm/ChatGPT/ChatGPT.cpp:387`
- Description: Response parse uses `DynamicJsonDocument doc(2000)`. Server payloads with long replies, `stackchan_action`, or function_call arguments easily exceed 2KB → parse failure → user hears “エラーです” despite a valid LLM answer. Vision markers / actions may also be lost.
- Suggestion: Use SPI RAM-backed document (existing `SpiRamJsonDocument`) sized for expected max (e.g. 8–32KB) or stream-parse only needed fields.
- Status: open

### Issue 14 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/services/discord_service.py:41`
- Description: Discord poll uses `limit=1` and `after=_last_message_id`, then processes only `messages[0]`. If multiple messages arrive between 60s polls, only one is handled and `_last_message_id` jumps to that message — **other messages are permanently dropped**.
- Suggestion: Use `limit=10+`, process in chronological order, advance `_last_message_id` per message.
- Status: open

### Issue 15 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/config.py:143`
- Description: `ANNOUNCEMENTS = json.loads(...)` at class body import time. Corrupt/partial `announcements.json` (or concurrent admin save mid-read) **crashes process import** and prevents server start. Same class of risk if admin writes invalid JSON without validation (`admin_routes.py:156`).
- Suggestion: Lazy-load with try/except; validate schema on save; fall back to `[]` on error and log.
- Status: open

### Issue 16 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/services/speech_service.py:40`
- Description: `subprocess.run([WHISPER_CLI, ...])` with default empty `WHISPER_CLI` / `WHISPER_MODEL` (`config.py:86–87`) raises `FileNotFoundError` / fails closed poorly; no validation at startup. Also no concurrency limit — parallel STT can thrash CPU.
- Suggestion: Fail fast at startup if CLI/model missing; serialize whisper with a lock or worker pool; return clear 503.
- Status: open

### Issue 17 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/firmware/src/main.cpp:80`
- Description: **Still open from 2026-07-02**: `lipSync` samples `getLevel()` every `delay(100)` (~10 Hz) with hard clip; `TTSBase::getLevel()` returns a single buffer sample (`tts/TTSBase.h:27`). Lip sync remains weak/choppy during MP3 push playback (playback uses shared `out` but level quality is still poor).
- Suggestion: Raise update rate (~20–30 ms), smooth envelope, RMS over buffer; ensure level is meaningful for `playMP3` path.
- Status: open

### Issue 18 -- Severity: bug
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/firmware/src/main.cpp:457`
- Description: **Still open**: `init_watchdog()` / `reset_watchdog()` remain commented out. Hang in HTTP download, camera, or STT leaves device frozen until power cycle. Soft WDT in `WatchDog.cpp` is 180s but unused.
- Suggestion: Re-enable with careful feeds around long blocking ops, or use ESP task WDT with longer timeout during known long I/O.
- Status: open

### Issue 19 -- Severity: suggestion
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/app.py:210`
- Description: Both `app.py` and `voice_server.py:273` bind `host="0.0.0.0"` with no auth on chat, STT, TTS generate, cleanup, vision. Fine for isolated LAN; dangerous on shared Wi‑Fi / port-forward. Docs (`docs/server.md:50`) encourage opening `:5050/admin` by IP.
- Suggestion: Default bind `127.0.0.1` for voice_server; optional `SERVER_BIND`; document firewall; add simple token header for device APIs.
- Status: open

### Issue 20 -- Severity: suggestion
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/ai_handler.py:50`
- Description: Global `_english_mode` dict is process-wide, not per-device/session, and unsynchronized. Multiple family users / concurrent chats flip mode for everyone; races under threaded Flask.
- Suggestion: Per-device or per-speaker mode in memory/file; at least use a lock; reset on timeout.
- Status: open

### Issue 21 -- Severity: suggestion
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/firmware/src/WebAPI.cpp:174`
- Description: Device HTTP APIs `/speech`, `/chat`, `/face`, `/play`, `/camera/trigger` are open on the LAN with no auth. `/speech` can force arbitrary TTS; `/camera/trigger` can force capture+upload.
- Suggestion: Shared LAN token, or accept only from configured server IP.
- Status: open

### Issue 22 -- Severity: suggestion
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/firmware/src/main.cpp:123`
- Description: **Still open (power)**: `battery_check` only toggles icon every 60s; no long-charge detection, voltage log, or power-policy. Overnight “power drop” remains undiagnosed in software.
- Suggestion: Log charge duration/level; optional USB/ext-output policy for TakaoBase; document hardware overcharge behavior.
- Status: open

### Issue 23 -- Severity: suggestion
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/admin_routes.py:460`
- Description: Unauthenticated `POST /admin/api/mlx/download` starts `snapshot_download` for any `model` string — arbitrary large HF downloads, disk fill, and bandwidth abuse on the host.
- Suggestion: Auth + allowlist of model IDs; disk free-space check; cancel/status API.
- Status: open

### Issue 24 -- Severity: suggestion
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/firmware/src/stt/Whisper.cpp:63`
- Description: When custom `base_url` is set (local server), TLS clients use `setInsecure()` (and ChatGPT path uses null CA for custom HTTPS). Local HTTP is fine; accidental `https://` custom URL disables cert verification.
- Suggestion: Prefer plain HTTP for LAN server; if HTTPS, pin a known CA or explicit insecure flag in YAML.
- Status: open

### Issue 25 -- Severity: suggestion
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/firmware/src/driver/PlayMP3.cpp:41`
- Description: Comment claims PSRAM allocation but code uses plain `malloc(30*1024)`. On memory pressure, internal heap fragmentation can fail fatally (`for(;;) delay`).
- Suggestion: Use `heap_caps_malloc(..., MALLOC_CAP_SPIRAM)` with fallback and non-fatal error path.
- Status: open

### Issue 26 -- Severity: suggestion
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/services/scheduler_service.py:125`
- Description: `setup_scheduler()` always calls `scheduler.start()`, and `app.py` also calls `start_scheduler()`. Currently idempotent via `running` check, but jobs are registered every `setup_scheduler` call without clearing — reload/re-import risk of duplicate jobs if ever called twice in one process.
- Suggestion: Guard job registration with a “configured” flag; never double-`add_job` for same id (use explicit `id=` + `replace_existing=True`).
- Status: open

### Issue 27 -- Severity: suggestion
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/Copy-to-SD/yaml/SC_BasicConfig.yaml:49`
- Description: `secret_config_show: true` defaults to logging personal/config secrets to serial — easy accidental leak during support/debug.
- Suggestion: Default `false` in shipped YAML; document when to enable.
- Status: open

### Issue 28 -- Severity: nit
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/services/speech_service.py:21`
- Description: Large commented debug blocks remain; previous review cleanup still incomplete. Harmless but noise for maintenance.
- Suggestion: Delete dead debug paths; keep a single env-gated debug dump if needed.
- Status: open

### Issue 29 -- Severity: nit
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/server/llm_client.py:23`
- Description: `load_dotenv()` without the `~/env` paths used in `config.py` / `voice_server.py` — secondary loads may miss keys if modules imported in isolation (partially mitigated because `config` loads first in app).
- Suggestion: Centralize env loading in one module; avoid duplicate/conflicting load_dotenv calls.
- Status: open

### Issue 30 -- Severity: nit
- File: `/Users/yukin_co/AI_assistant/StacChan_EX_Amigo/firmware/src/llm/ChatGPT/ChatGPT.cpp:352`
- Description: Empty `response` always calls `mark_camera_trigger_awaiting()` even when emptiness is not a camera trigger (e.g. unexpected empty content). Can suppress touch/UI for ~8s (`WebAPI.h` default timeout).
- Suggestion: Only mark awaiting when `camera_action.trigger` was true (pass flag through `execChatGpt` / chat loop).
- Status: open

## Fix-first priorities

1. **Lock down admin + secrets** (Issues 1–2, 23): auth on `/admin/*`, mask API keys, restrict MLX download.
2. **SSRF + path traversal** (Issues 3–5, 10): allowlist proxy `src`, sanitize voice/song/cache filenames, cap SPIFFS downloads.
3. **Serialize `today.md` writers** (Issue 6): process-wide lock / single writer so chat, weather, batch, vision cannot corrupt memory.
4. **English STT contract** (Issue 7): make `/v1/audio/transcriptions` honor English mode / EN model the way firmware actually calls it.
5. **MLX/VLM concurrency + vision queue** (Issues 8–9, 11–13): generate lock, vision backpressure, non-blocking play/camera on firmware, larger JSON doc for chat responses.

## Safe to start editing?

**Yes, with constraints.** New feature work is reasonable on **server-side conversation features** only after (or tightly alongside) auth, path sanitization, and `today.md` locking; firmware UX (lipSync, pet/tap, power diagnostics) can proceed in parallel because it does not widen the unauthenticated admin/SSRF surface. Avoid large multi-thread STT/always-on-mic features until whisper concurrency and device loop blocking are addressed.
