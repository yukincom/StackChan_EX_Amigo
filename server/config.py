# config.py
"""設定管理モジュール"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

_BASE_DIR = Path(__file__).resolve().parent
_ENV_DIR = Path(os.getenv("STACKCHAN_ENV_DIR", str(Path.home() / "env"))).expanduser()
_JSON_DIR = Path(os.getenv("STACKCHAN_JSON_DIR", str(_BASE_DIR / "json"))).expanduser()
_STATE_DIR = Path(
    os.getenv(
        "STACKCHAN_STATE_DIR",
        str(Path.home() / ".local" / "state" / "stackchan-ex-amigo"),
    )
).expanduser()
if not _STATE_DIR.is_absolute():
    _STATE_DIR = (_BASE_DIR / _STATE_DIR).resolve()
CONFIG_LOAD_ERRORS: list[str] = []
_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}

if os.getenv("STACKCHAN_SKIP_DOTENV", "").strip().lower() not in {"1", "true", "yes"}:
    load_dotenv(_ENV_DIR / ".env")
    load_dotenv(_ENV_DIR / ".env.local", override=True)


def _csv_env(key: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(key, default).split(",") if item.strip()]


def _int_env(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        CONFIG_LOAD_ERRORS.append(f"{key}: expected integer")
        return default


def _float_env(key: str, default: float) -> float:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        CONFIG_LOAD_ERRORS.append(f"{key}: expected number")
        return default


def _bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    if raw in _BOOL_TRUE:
        return True
    if raw in _BOOL_FALSE:
        return False
    CONFIG_LOAD_ERRORS.append(f"{key}: expected boolean")
    return default


def _path_env(key: str, default: Path) -> str:
    raw = os.getenv(key, "").strip()
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = (_BASE_DIR / path).resolve()
    return str(path)


def _load_json_list(path: Path, key: str) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        CONFIG_LOAD_ERRORS.append(f"{key}: invalid JSON ({path})")
        return []
    if not isinstance(data, list):
        CONFIG_LOAD_ERRORS.append(f"{key}: expected JSON array ({path})")
        return []
    return data

class Config:
    """アプリケーション設定"""
    LOAD_ERRORS = CONFIG_LOAD_ERRORS
    ENV_DIR = str(_ENV_DIR)
    JSON_DIR = str(_JSON_DIR)
    STATE_DIR = str(_STATE_DIR.resolve())
    TRACE_ENABLED = _bool_env("TRACE_ENABLED", True)
    TRACE_DIR = _path_env("TRACE_DIR", Path(STATE_DIR) / "trace")
    TRACE_MAX_BYTES = _int_env("TRACE_MAX_BYTES", 2_000_000)
    TRACE_KEEP_FILES = _int_env("TRACE_KEEP_FILES", 3)
    SUPPORT_BUNDLE_MAX_EVENTS = _int_env("SUPPORT_BUNDLE_MAX_EVENTS", 500)
    JOURNAL_DIR = _path_env("JOURNAL_DIR", Path(STATE_DIR) / "journal")
    # AI モデル設定
    AI_PROVIDER      = os.getenv("AI_PROVIDER", "MLX").strip().lower()
    AI_CHAT_MODEL    = os.getenv("AI_CHAT_MODEL", "mlx-community/Qwen3.6-35B-A3B-4bit")

    AI_SUMMARY_PROVIDER = os.getenv("AI_SUMMARY_PROVIDER", "MLX").strip().lower()
    AI_SUMMARY_MODEL = os.getenv("AI_SUMMARY_MODEL", "mlx-community/Qwen3.6-35B-A3B-4bit")

    AI_SEARCH_MODEL  = os.getenv("AI_SEARCH_MODEL",  "gemini-2.5-flash")  # 検索（Gemini固定）

    SEARCH_KEYWORDS = os.getenv("SEARCH_KEYWORDS", "調べて,しらべて,調査して,ちょうさして,サーチして,さーちして,ぐぐって,ググって").split(",")
    SPEECH_ALLOW_SHORT = os.getenv("SPEECH_ALLOW_SHORT", "はーい,いや,やだ,だめ,ねえ").split(",")
    
    THINKING_STRIP_PATTERNS = os.getenv(
        "THINKING_STRIP_PATTERNS", 
        "Final Response:,Response:"
    ).split(",")

    # AI出力設定
    AI_MAX_OUTPUT_TOKENS = _int_env("AI_MAX_OUTPUT_TOKENS", 1200)
    AI_RECENT_TURNS = _int_env("AI_RECENT_TURNS", 5)
    AI_SEARCH_RECENT_TURNS = _int_env("AI_SEARCH_RECENT_TURNS", 1)
    
    # 用途別temperature
    AI_CHAT_TEMPERATURE    = _float_env("AI_CHAT_TEMPERATURE", 0.8)  # 会話：豊か目に
    AI_SUMMARY_TEMPERATURE = _float_env("AI_SUMMARY_TEMPERATURE", 0.3)  # 要約：正確に
    AI_SEARCH_TEMPERATURE  = _float_env("AI_SEARCH_TEMPERATURE", 0.3)  # 検索：正確に

    # M5Stack
    M5STACK_IP   = os.getenv("M5STACK_IP",   "192.168.1.49")
    M5STACK_PORT = _int_env("M5STACK_PORT", 80)
    M5STACK_URL = os.getenv("M5STACK_URL", f"http://{M5STACK_IP}:{M5STACK_PORT}")
    M5STACK_TIMEOUT = _int_env("M5STACK_TIMEOUT", 5)
    VISION_TRANSIENT_KEYWORDS = _csv_env(
        "VISION_TRANSIENT_KEYWORDS",
        "見て,みて,これ見て,みてみて,見てください,みてください,look at this,see this,can you see this",
    )
    VISION_ARCHIVE_KEYWORDS = _csv_env(
        "VISION_ARCHIVE_KEYWORDS",
        "資料用に撮って,しりょうようにとって,撮影して,さつえいして,写真撮って,しゃしんとって,写真を撮って,しゃしんをとって,take a picture,take a photo,photo please,capture this",
    )
    VISION_ARCHIVE_DIR = os.getenv("VISION_ARCHIVE_DIR", str(Path.home() / "Desktop" / "stack_photo"))
    AI_VISION_TRANSIENT_MARKER = os.getenv("AI_VISION_TRANSIENT_MARKER", "[[VISION_TRANSIENT_TRIGGER]]")
    AI_VISION_ARCHIVE_MARKER = os.getenv("AI_VISION_ARCHIVE_MARKER", "[[VISION_ARCHIVE_TRIGGER]]")
    AI_VISION_TRIGGER_ENDPOINT = os.getenv("AI_VISION_TRIGGER_ENDPOINT", "vision_ai_trigger")


    # Whisper.cpp settings
    WHISPER_CLI   = os.getenv("WHISPER_CLI", "")
    WHISPER_MODEL = os.getenv("WHISPER_MODEL", "")
    WHISPER_NO_SPEECH_THOLD = _float_env("WHISPER_NO_SPEECH_THOLD", 0.6)
    WHISPER_MODEL_EN = os.getenv("WHISPER_MODEL_EN", "")

    # Voice Server settings(VOICE VOX)
    VOICE_SERVER_URL = os.getenv("VOICE_SERVER_URL", "http://127.0.0.1:5001")
    VOICE_REQUEST_TIMEOUT = _int_env("VOICE_REQUEST_TIMEOUT", 30)
    VOICE_STORAGE_DIR = _path_env("VOICE_STORAGE_DIR", _BASE_DIR / "voice_store")

    # VOICEVOX settings
    VOICEVOX_URL = os.getenv("VOICEVOX_URL", "http://localhost:50021")
    VOICEVOX_SPEAKER_ID = _int_env("VOICEVOX_SPEAKER_ID", 2)
    PC_CACHE_AUDIO_DIR = _path_env("PC_CACHE_AUDIO_DIR", Path(VOICE_STORAGE_DIR) / "cache")
    STACK_SD_AUDIO_DIR = _path_env("STACK_SD_AUDIO_DIR", _BASE_DIR / "Copy-to-SD" / "stack_sd_audio")

    # 音声の最小バイト数（マジックナンバー）
    SPEECH_MIN_BYTES = _int_env("SPEECH_MIN_BYTES", 10000)

    # Render settings
    POLL_INTERVAL = 60
    RENDER_URL = os.getenv("RENDER_URL", "")

    #SERVER setting
    SERVER_PORT = _int_env("SERVER_PORT", 5050)
    # ConfigUI (/admin) 用。未設定時は localhost のみ許可。
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

    # パーソナル設定
    ASSISTANT_NAME   = os.getenv("ASSISTANT_NAME", "スタックちゃん")
    FAMILY_DEFAULT   = os.getenv("FAMILY_DEFAULT", "")
    ASSISTANT_PERSONA = os.getenv(
        "ASSISTANT_PERSONA",
        ""
    )

    # 通知サービス設定
    NOTIFICATION_SERVICE = os.getenv("NOTIFICATION_SERVICE", "line")
    # Discord 設定
    DISCORD_BOT_TOKEN    = os.getenv("DISCORD_BOT_TOKEN", "")
    DISCORD_CHANNEL_ID   = os.getenv("DISCORD_CHANNEL_ID", "")

    # Memory settings（basic-memory）
    MEMORY_DIR = _path_env("MEMORY_DIR", _BASE_DIR / "memory")
    ARCHIVE_DIR = _path_env("ARCHIVE_DIR", _BASE_DIR / "archive")
    SUMMARY_KEEP_DAYS = _int_env("SUMMARY_KEEP_DAYS", 14)
    # 天気設定
    WEATHER_LATITUDE  = os.getenv("WEATHER_LATITUDE",  "")
    WEATHER_LONGITUDE = os.getenv("WEATHER_LONGITUDE", "")
    WEATHER_KEYWORDS_TODAY    = os.getenv("WEATHER_KEYWORDS_TODAY",    "今日の天気,きょうのてんき").split(",")
    WEATHER_KEYWORDS_TOMORROW = os.getenv("WEATHER_KEYWORDS_TOMORROW", "明日の天気,あしたのてんき").split(",")
    WEATHER_MORNING_HOUR      = _int_env("WEATHER_MORNING_HOUR", 7)
    WEATHER_MORNING_MINUTE    = _int_env("WEATHER_MORNING_MINUTE", 10)
    WEATHER_FORECAST_HOUR     = _int_env("WEATHER_FORECAST_HOUR", 17)
    WEATHER_FORECAST_MINUTE   = _int_env("WEATHER_FORECAST_MINUTE", 0)
    WEATHER_CONFIRMED_HOUR    = _int_env("WEATHER_CONFIRMED_HOUR", 19)
    WEATHER_CONFIRMED_MINUTE  = _int_env("WEATHER_CONFIRMED_MINUTE", 0)
    # アナウンス設定（announcements.jsonから読み込み）
    _announcements_path = Path(_path_env("ANNOUNCEMENTS_FILE", _JSON_DIR / "announcements.json"))
    ANNOUNCEMENTS = _load_json_list(_announcements_path, "ANNOUNCEMENTS_FILE")

    # 歌機能
    SONG_TRIGGER = os.getenv("SONG_TRIGGER", "歌って,うたって,歌ってください").split(",")

    SONG_MAP = {}
    for item in os.getenv("SONG_MAP", "").split(","):
        if ":" in item:
            keys, filename = item.rsplit(":", 1)
            for key in keys.split("|"):
                SONG_MAP[key.strip()] = filename.strip()

# インスタンス
config = Config()
