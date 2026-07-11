"""キャッシュ音声カタログ管理"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

from config import config
from path_safety import resolve_under, safe_id

CATALOG_PATH = Path(__file__).resolve().parent.parent / "json" / "voice_cache_catalog.json"
EXAMPLE_CATALOG_PATH = Path(__file__).resolve().parent.parent / "json_example" / "voice_cache_catalog.json"

def _empty_catalog() -> dict:
    return {"pc_cache": [], "stack_sd": []}


def _normalize_items(items) -> list[dict]:
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _normalize_catalog(data) -> dict:
    raw = data if isinstance(data, dict) else {}
    return {
        "pc_cache": _normalize_items(raw.get("pc_cache")),
        "stack_sd": _normalize_items(raw.get("stack_sd")),
    }


def load_catalog() -> dict:
    if CATALOG_PATH.exists():
        return _normalize_catalog(json.loads(CATALOG_PATH.read_text(encoding="utf-8")))

    if EXAMPLE_CATALOG_PATH.exists():
        catalog = _normalize_catalog(json.loads(EXAMPLE_CATALOG_PATH.read_text(encoding="utf-8")))
        save_catalog(catalog)
        return catalog

    return _empty_catalog()


def save_catalog(data: dict) -> dict:
    catalog = _normalize_catalog(data)
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return catalog


def get_pc_cache_audio_path(filename: str) -> Path:
    name = safe_id(filename)
    if not name:
        raise ValueError(f"invalid cache filename: {filename!r}")
    return resolve_under(config.PC_CACHE_AUDIO_DIR, f"{name}.mp3")


def get_stack_sd_audio_path(filename: str) -> Path:
    name = safe_id(filename)
    if not name:
        raise ValueError(f"invalid cache filename: {filename!r}")
    return resolve_under(config.STACK_SD_AUDIO_DIR, f"{name}.mp3")


def get_pc_cache_text(item_id: str, default: str = "") -> str:
    safe_id = str(item_id or "").strip()
    if not safe_id:
        return default

    for item in load_catalog().get("pc_cache", []):
        if str(item.get("id", "")).strip() != safe_id:
            continue
        text = str(item.get("text", "")).strip()
        return text or default
    return default


def _wav_to_mp3_bytes(wav_data: bytes) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")

    src_path = dst_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as src:
            src.write(wav_data)
            src_path = Path(src.name)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as dst:
            dst_path = Path(dst.name)

        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(src_path),
                "-vn",
                "-acodec",
                "libmp3lame",
                "-b:a",
                "128k",
                str(dst_path),
            ],
            check=True,
            capture_output=True,
        )
        return dst_path.read_bytes()
    finally:
        for path in (src_path, dst_path):
            if path and path.exists():
                path.unlink()


def _generate_source_url(text: str) -> str:
    response = requests.post(
        f"{config.VOICE_SERVER_URL}/generate_mixed",
        json={"text": text, "speaker_id": config.VOICEVOX_SPEAKER_ID},
        timeout=config.VOICE_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "voice generation failed")
    download_path = result.get("download_path", f"/voice/{result['voice_id']}")
    return f"{config.VOICE_SERVER_URL}{download_path}"


def generate_mp3_file(text: str, output_path: Path) -> Path:
    source_url = _generate_source_url(text)
    wav_response = requests.get(source_url, timeout=30)
    wav_response.raise_for_status()
    mp3_data = _wav_to_mp3_bytes(wav_response.content)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(mp3_data)
    return output_path
