# StackChan_EX_Amigo — agent notes

## Layout

- `firmware/` — M5Stack CoreS3 (PlatformIO). Flash sparingly; prefer server-side fixes when possible.
- `server/` — Flask app (`app.py`), TTS process (`voice_server.py`), ConfigUI.
- `Copy-to-SD/` — YAML and canned audio for the device SD card.
- Secrets live in `~/env/.env` and optional override `~/env/.env.local` (not in this repo).

Related home “dev / daily driver” server tree may live separately as `yuno-chan-api` (GitHub `dev_StackChan_EX_Amig`). Prefer testing server changes there when hardware is attached; port stable fixes back here.

## ADMIN_TOKEN (ConfigUI)

ConfigUI is at `http://<host>:5050/admin`. All `/admin/api/*` routes require auth.

| `ADMIN_TOKEN` | Behavior |
|---------------|----------|
| **Unset / empty** | Only **localhost** may call admin APIs (401 from LAN). |
| **Set** | Required for **all** clients, including localhost. |

### How to set

```bash
# Generate
openssl rand -hex 32

# Put in ~/env/.env or ~/env/.env.local (see .env.example)
ADMIN_TOKEN=<generated-hex>
```

Restart `python app.py` after changing env.

Browser: open ConfigUI and enter the same token (sent as `Authorization: Bearer …`).  
CLI: `-H "Authorization: Bearer $ADMIN_TOKEN"` or `X-Admin-Token`.

Password-type env fields are masked on GET; empty POST value means “leave unchanged”.

Do not commit real tokens. Do not log `ADMIN_TOKEN` or API keys.

## Server security / reliability (already in tree)

- Admin auth + secret masking (`admin_routes`, ConfigUI JS).
- Audio proxy SSRF allowlist + path sanitization (`path_safety`, voice/song/cache).
- `today.md` serialized via `memory_manager` lock.
- English mode: `/v1/audio/transcriptions` uses EN Whisper when `is_english_mode()`; chat forces English reply in that mode.
- MLX text generate under `_mlx_generate_lock`; `/vision/upload` uses a small queue (429 when full).

## Firmware policy

Batch hardware-related changes; minimize flash cycles. Server-only behavior changes should not require a flash.
