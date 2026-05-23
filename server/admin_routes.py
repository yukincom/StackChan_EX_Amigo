# admin_routes.py
"""ConfigUI用 Blueprint"""

import os
import json
from pathlib import Path
from flask import Blueprint, jsonify, render_template, request
from config import config
from services.voice_cache_catalog import (
    generate_mp3_file,
    get_pc_cache_audio_path,
    get_stack_sd_audio_path,
    load_catalog,
    save_catalog,
)

admin_bp = Blueprint("admin", __name__, template_folder="templates")

_BASE = Path(__file__).parent
_ENV_DIR = Path.home() / "env" 
_JSON_DIR = _BASE / "json"
_MEMBER_JSON_PATH = _JSON_DIR / "member.json"
_READING_MAP_JSON_PATH = _JSON_DIR / "reading_map.json"
_ANNOUNCEMENTS_JSON_PATH = _JSON_DIR / "announcements.json"

# ===== ヘルパー =====

def _read_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))

def _write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_master_member() -> dict:
    return {
        "name": "",
        "notes": "",
        "interests": [],
        "line_user_id": "",
        "discord_user_id": "",
    }


def _normalize_member_payload(data) -> dict:
    raw = data if isinstance(data, dict) else {}

    master = raw.get("master")
    if not isinstance(master, dict):
        master = _default_master_member()

    family = raw.get("family", [])
    if not isinstance(family, list):
        family = []

    friends = raw.get("friends", [])
    if not isinstance(friends, list):
        friends = []

    return {
        "master": master,
        "family": family,
        "friends": friends,
    }


def _display_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        rel = resolved.relative_to(_BASE.resolve())
        return f"./{rel.as_posix()}"
    except ValueError:
        return str(resolved)


def _voice_cache_paths_payload() -> dict:
    pc_dir = Path(config.PC_CACHE_AUDIO_DIR).expanduser().resolve()
    sd_dir = Path(config.STACK_SD_AUDIO_DIR).expanduser().resolve()
    pc_default = (_BASE / "voice_store" / "cache").resolve()
    sd_default = (_BASE / "Copy-to-SD" / "stack_sd_audio").resolve()
    return {
        "pc_cache_dir": str(pc_dir),
        "pc_cache_dir_display": _display_path(pc_dir),
        "pc_cache_default_display": _display_path(pc_default),
        "pc_cache_is_default": pc_dir == pc_default,
        "stack_sd_dir": str(sd_dir),
        "stack_sd_dir_display": _display_path(sd_dir),
        "stack_sd_default_display": _display_path(sd_default),
        "stack_sd_is_default": sd_dir == sd_default,
    }


# ===== 管理画面 =====

@admin_bp.route("/admin")
def admin_index():
    return render_template("admin/index.html")


# ===== member.json =====

@admin_bp.route("/admin/api/member", methods=["GET"])
def get_member():
    data = _normalize_member_payload(_read_json(_MEMBER_JSON_PATH))
    if data is None:
        data = {"master": _default_master_member(), "family": [], "friends": []}
    return jsonify(data)

@admin_bp.route("/admin/api/member", methods=["POST"])
def save_member():
    try:
        _MEMBER_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        _write_json(_MEMBER_JSON_PATH, _normalize_member_payload(request.get_json(force=True)))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ===== reading_map.json =====

@admin_bp.route("/admin/api/reading_map", methods=["GET"])
def get_reading_map():
    data = _read_json(_READING_MAP_JSON_PATH)
    if data is None:
        data = {}
    items = [{"word": k, "reading": v} for k, v in data.items()]
    return jsonify(items)

@admin_bp.route("/admin/api/reading_map", methods=["POST"])
def save_reading_map():
    try:
        items = request.get_json(force=True)  # [{word, reading}, ...]
        data = {item["word"]: item["reading"] for item in items if item.get("word")}
        _READING_MAP_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        _write_json(_READING_MAP_JSON_PATH, data)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ===== announcements.json =====

@admin_bp.route("/admin/api/announcements", methods=["GET"])
def get_announcements():
    data = _read_json(_ANNOUNCEMENTS_JSON_PATH)
    if data is None:
        data = []
    return jsonify(data)

@admin_bp.route("/admin/api/announcements", methods=["POST"])
def save_announcements():
    try:
        _ANNOUNCEMENTS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        _write_json(_ANNOUNCEMENTS_JSON_PATH, request.get_json(force=True))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ===== .env =====

ENV_GROUPS = [
    {
        "group": "🎤 音声認識 / Whisper.cpp",
        "items": [
            {"key": "WHISPER_CLI",             "label": "Whisper CLIパス",  "type": "text"},
            {"key": "",                        "label": "",                 "type": "empty"},
            {"key": "WHISPER_MODEL",           "label": "日本語モデルパス", "type": "text"},
            {"key": "WHISPER_MODEL_EN",        "label": "英語モデルパス",   "type": "text"},
            {"key": "WHISPER_NO_SPEECH_THOLD", "label": "無音判定しきい値", "type": "number"},
            {"key": "SPEECH_MIN_BYTES",        "label": "最小音声バイト数", "type": "number"},
            {"key": "SPEECH_ALLOW_SHORT",      "label": "短文許可ワード（カンマ区切り）", "type": "text"},
            {"key": "",                        "label": "",                 "type": "empty"},
        ]
    },
    {
        "group": "🔊 音声合成 / VOICEVOX, KOKORO",
        "items": [
            {"key": "VOICEVOX_URL",        "label": "VOICEVOX URL",  "type": "text"},
            {"key": "VOICE_SERVER_URL",    "label": "音声サーバーURL", "type": "text"},
            {"key": "VOICEVOX_SPEAKER_ID", "label": "日本語音声 / VOICEVOX", "type": "datalist",
             "options": [
                "2:四国めたん（ノーマル）",
                "3:ずんだもん（ノーマル）",
                "8:春日部つむぎ（ノーマル）",
                "13:青山龍星（ノーマル）",
                "12:白上虎太郎（ノーマル）",
                "10:雨晴はう（ノーマル）",
             ]},
            {"key": "KOKORO_VOICE_ENGLISH", "label": "英語音声 / KOKORO", "type": "datalist",
             "options": ["af_sarah","af_sky","af_bella","af_nicole","am_adam","am_michael"]},
            {"key": "VOICE_STORAGE_DIR",   "label": "音声ファイル保存先", "type": "text"},
            {"key": "PC_CACHE_AUDIO_DIR",  "label": "PCキャッシュ音声保存先", "type": "text"},
            {"key": "STACK_SD_AUDIO_DIR",  "label": "Stack SD音声書き出し先", "type": "text"},
            {"key": "VOICE_REQUEST_TIMEOUT", "label": "音声サーバー タイムアウト（秒）", "type": "number"},
            {"key": "",                    "label": "",               "type": "empty"},
        ]
    },
    {
        "group": "🌤️ 天気設定 / Open-Meteo",
        "items": [
            {"key": "WEATHER_LATITUDE",  "label": "緯度", "type": "text"},
            {"key": "WEATHER_LONGITUDE", "label": "経度", "type": "text"},
        ]
    },
    {
        "group": "🔖 記憶設定 / Basic Memory",
        "items": [
            {"key": "MEMORY_DIR",        "label": "記憶ディレクトリ",       "type": "text"},
            {"key": "ARCHIVE_DIR",       "label": "アーカイブディレクトリ", "type": "text"},
            {"key": "SUMMARY_KEEP_DAYS", "label": "要約保持日数",           "type": "number"},
        ]
    },
    {
        "group": "📱 通信設定 / LINE_Render,Discord（空欄可）",
        "items": [
            {"key": "DISCORD_BOT_TOKEN",  "label": "Discord Botトークン",  "type": "password"},
            {"key": "DISCORD_CHANNEL_ID", "label": "Discord チャンネルID", "type": "text"},
            {"key": "RENDER_URL",         "label": "Render URL",           "type": "text"},
        ]
    },
    {
        "group": "🤖 Stack設定 / Vision",
        "items": [
            {"key": "M5STACK_IP",                "label": "StackChan IP",                        "type": "text"},
            {"key": "M5STACK_PORT",              "label": "StackChan ポート",                    "type": "number"},
            {"key": "M5STACK_URL",               "label": "StackChan URL",                       "type": "text"},
            {"key": "M5STACK_TIMEOUT",           "label": "StackChan タイムアウト（秒）",        "type": "number"},
            {"key": "VISION_TRANSIENT_KEYWORDS", "label": "一時利用カメラキーワード（カンマ区切り）", "type": "text"},
            {"key": "VISION_ARCHIVE_KEYWORDS",   "label": "資料保存カメラキーワード（カンマ区切り）", "type": "text"},
            {"key": "VISION_ARCHIVE_DIR",        "label": "資料画像の保存先フォルダ",             "type": "text"},
            {"key": "",                          "label": "",                                   "type": "empty"},
        ]
    },
    {
        "group": "⚙️ サーバー設定",
        "items": [
            {"key": "SERVER_PORT", "label": "サーバーポート", "type": "number"},
        ]
    },
]

WEATHER_SCHEDULE_ENV_GROUPS = [
    {
        "group": "🌤️ 天気取得時刻 / Open-Meteo",
        "items": [
            {"key": "WEATHER_MORNING_HOUR",     "label": "今日予報の取得時",   "type": "number"},
            {"key": "WEATHER_MORNING_MINUTE",   "label": "今日予報の取得分",   "type": "number"},
            {"key": "WEATHER_FORECAST_HOUR",    "label": "明日予報の取得時",   "type": "number"},
            {"key": "WEATHER_FORECAST_MINUTE",  "label": "明日予報の取得分",   "type": "number"},
            {"key": "WEATHER_CONFIRMED_HOUR",   "label": "当日確定天気の取得時", "type": "number"},
            {"key": "WEATHER_CONFIRMED_MINUTE", "label": "当日確定天気の取得分", "type": "number"},
        ]
    },
]
# ===== AI設定専用グループ =====
AI_ENV_GROUPS = [
    {
        "group": "🤖 アシスタント基本設定",
        "items": [
            {"key": "ASSISTANT_NAME",    "label": "アシスタント名", "type": "text"},
            {"key": "ASSISTANT_PERSONA", "label": "ペルソナ設定",   "type": "textarea"},
        ]
    },
    {
        "group": "🔀 AIプロバイダー設定",
        "items": [
            {"key": "AI_PROVIDER", "label": "会話プロバイダー", "type": "select",
             "options": [
                 "gemini:Gemini",
                 "openai:OpenAI互換（Grok / Ollama / OpenRouter）",
                 "mlx:MLX"
             ]},
            {"key": "AI_CHAT_MODEL",    "label": "会話モデル",              "type": "text"},
            {"key": "AI_SUMMARY_PROVIDER", "label": "要約プロバイダー", "type": "select",
             "options": [
                 "gemini:Gemini",
                 "openai:OpenAI互換（Grok / Ollama / OpenRouter）",
                 "mlx:MLX"
             ]},                
            {"key": "AI_SUMMARY_MODEL", "label": "要約モデル",              "type": "text"},        
            {"key": "AI_SEARCH_MODEL",  "label": "検索モデル（Gemini固定）", "type": "text"},
            {"key": "VLM_MODEL",        "label": "Vision モデル（MLX VLM）", "type": "text"},
        ]
    },
    {
        "group": "🔢 生成パラメータ",
        "items": [
            {"key": "AI_MAX_OUTPUT_TOKENS",   "label": "最大出力トークン数",                        "type": "number"},
            {"key": "AI_RECENT_TURNS",        "label": "会話記憶ターン数（推奨：10未満）",           "type": "number"},
            {"key": "AI_CHAT_TEMPERATURE",    "label": "会話 Temperature（豊かさ・デフォルト 0.8）", "type": "number"},
            {"key": "AI_SUMMARY_TEMPERATURE", "label": "要約 Temperature（正確さ・デフォルト 0.3）", "type": "number"},
            {"key": "AI_SEARCH_TEMPERATURE",  "label": "検索 Temperature（正確さ・デフォルト 0.3）", "type": "number"},
        ]
    },
    {
        "group": "🔑 APIキー",
        "items": [
            {"key": "GEMINI_API_KEY",  "label": "Gemini API キー（検索機能のため常に必須）",       "type": "password"},
            {"key": "",                      "label": "",                      "type": "empty"},
            {"key": "OPENAI_BASE_URL", "label": "OpenAI互換 Base URL（AI_PROVIDER=openai の時）", "type": "text"},
            {"key": "OPENAI_API_KEY",  "label": "OpenAI互換 API キー（AI_PROVIDER=openai の時）", "type": "password"},        ]
    },
    {
        "group": "🔍 検索設定",
        "items": [
            {"key": "SEARCH_KEYWORDS", "label": "検索トリガーワード（カンマ区切り）", "type": "text"},
        ]
    },
    {
        "group": "💭 Thinkingモデル設定",
        "items": [
            {"key": "THINKING_STRIP_PATTERNS", 
             "label": "思考プロセス除去ワード（カンマ区切り・複数OK）キーワード以前の文章を除外します。例：FInal Response:,Response:", 
             "type": "text"},
        ]
    },
]

def _write_dotenv(path: Path, updates: dict[str, str]):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                new_lines.append(f'{k}={updates[k]}')
                updated_keys.add(k)
                continue
        new_lines.append(line)
    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f'{k}={v}')
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

def _build_env_response(groups):
    result = []
    for group in groups:
        items = [{**item, "value": os.getenv(item["key"], "")} for item in group["items"]]
        result.append({"group": group["group"], "items": items})
    return result


@admin_bp.route("/admin/api/env", methods=["GET"])
def get_env():
    return jsonify(_build_env_response(ENV_GROUPS))

@admin_bp.route("/admin/api/env", methods=["POST"])
def save_env():
    try:
        _write_dotenv(_ENV_DIR / ".env.local", request.get_json(force=True))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@admin_bp.route("/admin/api/weather_schedule_env", methods=["GET"])
def get_weather_schedule_env():
    return jsonify(_build_env_response(WEATHER_SCHEDULE_ENV_GROUPS))


@admin_bp.route("/admin/api/weather_schedule_env", methods=["POST"])
def save_weather_schedule_env():
    try:
        _write_dotenv(_ENV_DIR / ".env.local", request.get_json(force=True))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@admin_bp.route("/admin/api/ai_env", methods=["GET"])
def get_ai_env():
    return jsonify(_build_env_response(AI_ENV_GROUPS))

@admin_bp.route("/admin/api/ai_env", methods=["POST"])
def save_ai_env():
    try:
        _write_dotenv(_ENV_DIR / ".env.local", request.get_json(force=True))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500    


@admin_bp.route("/admin/api/voice_cache", methods=["GET"])
def get_voice_cache():
    return jsonify(
        {
            "catalog": load_catalog(),
            "paths": _voice_cache_paths_payload(),
        }
    )


@admin_bp.route("/admin/api/voice_cache", methods=["POST"])
def save_voice_cache():
    try:
        catalog = save_catalog(request.get_json(force=True) or {})
        return jsonify(
            {
                "ok": True,
                "catalog": catalog,
                "paths": _voice_cache_paths_payload(),
            }
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@admin_bp.route("/admin/api/voice_cache/generate", methods=["POST"])
def generate_voice_cache():
    try:
        payload = request.get_json(force=True) or {}
        kind = str(payload.get("kind", "")).strip()
        item = payload.get("item") or {}
        text = str(item.get("text", "")).strip()
        filename = str(item.get("filename", "")).strip()
        if not kind or not text or not filename:
            return jsonify({"ok": False, "error": "kind, text, filename are required"}), 400

        if kind == "pc_cache":
            output_path = generate_mp3_file(text, get_pc_cache_audio_path(filename))
        elif kind == "stack_sd":
            output_path = generate_mp3_file(text, get_stack_sd_audio_path(filename))
        else:
            return jsonify({"ok": False, "error": "unknown kind"}), 400

        return jsonify({"ok": True, "path": str(output_path), "size": output_path.stat().st_size})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    
# ===== MLXモデル チェック / ダウンロード =====
import threading
from huggingface_hub import try_to_load_from_cache, snapshot_download, _CACHED_NO_EXIST

_download_status = {}  # model_id → "downloading" | "done" | "error"

def _is_mlx_model_cached(model_id: str) -> bool:
    result = try_to_load_from_cache(model_id, "config.json")
    return result is not None and result is not _CACHED_NO_EXIST

@admin_bp.route("/admin/api/mlx/check")
def mlx_check():
    model = request.args.get("model", "").strip()
    if not model:
        return jsonify({"ok": False, "error": "model is empty"})
    cached = _is_mlx_model_cached(model)
    status = _download_status.get(model)
    return jsonify({"ok": True, "cached": cached, "status": status})

@admin_bp.route("/admin/api/mlx/download", methods=["POST"])
def mlx_download():
    model = (request.get_json(force=True) or {}).get("model", "").strip()
    if not model:
        return jsonify({"ok": False, "error": "model is empty"})
    if _download_status.get(model) == "downloading":
        return jsonify({"ok": True, "message": "already downloading"})

    def _do_download(model_id):
        _download_status[model_id] = "downloading"
        try:
            snapshot_download(repo_id=model_id)
            _download_status[model_id] = "done"
        except Exception as e:
            _download_status[model_id] = "error"
            print(f"[DOWNLOAD] ❌ {model_id}: {e}")

    threading.Thread(target=_do_download, args=(model,), daemon=True).start()
    return jsonify({"ok": True})    
