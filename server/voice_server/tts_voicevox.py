# voice_server/tts_voicevox.py
"""VOICEVOX TTS ロジック"""
from __future__ import annotations
import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

_ENV_DIR = Path.home() / "env"
load_dotenv(_ENV_DIR / ".env")
load_dotenv(_ENV_DIR / ".env.local", override=True)

# voice_server/ の親 = AI_assistant/ ルート
_BASE = Path(__file__).parent.parent

VOICEVOX_URL             = os.getenv("VOICEVOX_URL", "http://localhost:50021")
VOICEVOX_SPEAKER_ID      = int(os.getenv("VOICEVOX_SPEAKER_ID", "2"))
VOICEVOX_SPEAKER_KOMA_ID = int(os.getenv("VOICEVOX_SPEAKER_KOMA_ID", "13"))

_READING_MAP_PATH = _BASE / "json" / "reading_map.json"
_reading_map_cache: dict[str, str] = {}
_reading_map_mtime_ns: int | None = None


def _load_reading_map() -> dict[str, str]:
    """reading_map.json を必要時に再読込する"""
    global _reading_map_cache, _reading_map_mtime_ns

    try:
        mtime_ns = _READING_MAP_PATH.stat().st_mtime_ns
    except FileNotFoundError:
        _reading_map_cache = {}
        _reading_map_mtime_ns = None
        return {}

    if _reading_map_mtime_ns == mtime_ns:
        return _reading_map_cache

    try:
        data = json.loads(_READING_MAP_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _reading_map_cache

    if not isinstance(data, dict):
        data = {}

    _reading_map_cache = {
        str(word): str(reading)
        for word, reading in data.items()
        if str(word)
    }
    _reading_map_mtime_ns = mtime_ns
    return _reading_map_cache


def normalize_text(text: str) -> str:
    """VOICEVOXに渡す前にテキストを正規化"""
    reading_map = _load_reading_map()
    for word, reading in sorted(reading_map.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(word, reading)
    return text


def generate_voicevox_wav(text: str, speaker_id: int | None = None) -> bytes | None:
    """VOICEVOXでテキストをWAVに変換する"""
    sid = speaker_id if speaker_id is not None else VOICEVOX_SPEAKER_ID

    print(f"[1/2] audio_query生成中... speaker={sid}")
    query_response = requests.post(
        f"{VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": sid},
        timeout=10,
    )
    query_response.raise_for_status()
    audio_query = query_response.json()
    print(f"  outputSamplingRate: {audio_query.get('outputSamplingRate')} Hz")

    print("[2/2] WAV合成中...")
    synthesis_response = requests.post(
        f"{VOICEVOX_URL}/synthesis",
        params={"speaker": sid},
        json=audio_query,
        timeout=30,
    )
    synthesis_response.raise_for_status()
    return synthesis_response.content
