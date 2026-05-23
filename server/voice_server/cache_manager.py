"""管理UIベースのPCキャッシュ音声管理"""

from __future__ import annotations

import hashlib
from pathlib import Path

from config import config
from services.voice_cache_catalog import get_pc_cache_audio_path, load_catalog
from voice_server.tts_voicevox import normalize_text

VOICE_STORAGE_DIR = str(Path(config.VOICE_STORAGE_DIR).expanduser())


def get_cache_path(key: str) -> str:
    return str(get_pc_cache_audio_path(key))


def check_cache(text: str) -> dict | None:
    """管理UIで生成済みのPCキャッシュ音声があれば返す。"""
    normalized_text = normalize_text(text)
    for item in load_catalog().get("pc_cache", []):
        phrase = str(item.get("text", "")).strip()
        filename = str(item.get("filename", "")).strip()
        if not phrase or not filename:
            continue
        if normalize_text(phrase) != normalized_text:
            continue

        cache_path = Path(get_cache_path(filename))
        if not cache_path.exists():
            return None

        mp3_data = cache_path.read_bytes()
        return {
            "success": True,
            "voice_id": f"cache_{filename}",
            "size": len(mp3_data),
            "sha256": hashlib.sha256(mp3_data).hexdigest(),
            "download_path": f"/voice/cache_{filename}",
            "settings": {"text": text, "engine": "pc_cache_mp3"},
        }
    return None


def warmup_cache() -> None:
    """旧互換。キャッシュは管理UIから手動生成する。"""
    return None
