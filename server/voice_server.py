# voice_server.py 分割
"""音声生成サーバー (VOICEVOX + Kokoro)"""
from __future__ import annotations
import hashlib
import os
import time
import uuid

import requests
from flask import Flask, jsonify, request, send_file
from pathlib import Path
from dotenv import load_dotenv

from voice_server.tts_voicevox import (
    generate_voicevox_wav, normalize_text,
    VOICEVOX_URL, VOICEVOX_SPEAKER_ID,
)
from voice_server.tts_kokoro import (
    kokoro_tts, split_text_by_lang, concat_wav_bytes,
    KOKORO_VOICE_ENGLISH,
)
from voice_server.cache_manager import check_cache, VOICE_STORAGE_DIR, get_cache_path
from path_safety import resolve_under, safe_id, safe_voice_id

_ENV_DIR = Path.home() / "env"
load_dotenv(_ENV_DIR / ".env")
load_dotenv(_ENV_DIR / ".env.local", override=True)

SONGS_DIR = os.path.join(os.path.dirname(__file__), "songs")

app = Flask(__name__)


def _ensure_voice_storage_dir() -> None:
    Path(VOICE_STORAGE_DIR).mkdir(parents=True, exist_ok=True)


# ── ヘルスチェック ───────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    try:
        r = requests.get(f"{VOICEVOX_URL}/version", timeout=3)
        voicevox_ok      = r.status_code == 200
        voicevox_version = r.text.strip('"') if voicevox_ok else "unreachable"
    except Exception:
        voicevox_ok, voicevox_version = False, "unreachable"

    return jsonify({
        "status":           "ok",
        "service":          "voice_generator_voicevox",
        "storage":          VOICE_STORAGE_DIR,
        "voicevox_url":     VOICEVOX_URL,
        "voicevox_status":  "ok" if voicevox_ok else "error",
        "voicevox_version": voicevox_version,
        "speaker_id":       VOICEVOX_SPEAKER_ID,
    })


# ── 日本語TTS（VOICEVOX）────────────────────────────────
@app.route("/generate", methods=["POST"])
def generate():
    try:
        _ensure_voice_storage_dir()
        data       = request.get_json(silent=True) or {}
        text       = data.get("text", "").strip()
        speaker_id = int(data.get("speaker_id", VOICEVOX_SPEAKER_ID))

        if not text:
            return jsonify({"success": False, "error": "text is required"}), 400

        cached = check_cache(text)
        if cached:
            return jsonify(cached)

        def clip_at_sentence(t: str, max_chars: int = 500) -> str:
            if len(t) <= max_chars:
                return t
            for i in range(max_chars, 0, -1):
                if t[i] in ['。', '！', '？', '!', '?']:
                    return t[:i + 1]
            return t[:max_chars]

        clipped = normalize_text(clip_at_sentence(text))
        print(f"\n{'='*60}\n[VOICE] {len(clipped)}文字 speaker={speaker_id}\n{'='*60}")

        wav_data = generate_voicevox_wav(clipped, speaker_id)
        if not wav_data:
            return jsonify({"success": False, "error": "VOICEVOX synthesis failed"}), 500

        voice_id    = f"{int(time.time())}_{uuid.uuid4().hex}"
        sha256_hash = hashlib.sha256(wav_data).hexdigest()
        with open(os.path.join(VOICE_STORAGE_DIR, f"{voice_id}.wav"), "wb") as f:
            f.write(wav_data)

        print(f"[完了] {voice_id} / {len(wav_data):,} bytes")
        return jsonify({
            "success":       True,
            "voice_id":      voice_id,
            "size":          len(wav_data),
            "sha256":        sha256_hash,
            "download_path": f"/voice/{voice_id}",
            "settings":      {"text": clipped, "speaker_id": speaker_id, "engine": "voicevox"},
        })

    except requests.exceptions.ConnectionError:
        return jsonify({"success": False, "error": f"VOICEVOX unreachable: {VOICEVOX_URL}"}), 503
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ── 英語TTS（Kokoro）────────────────────────────────────
@app.route("/generate_en", methods=["POST"])
def generate_en():
    try:
        _ensure_voice_storage_dir()
        data  = request.get_json(silent=True) or {}
        text  = data.get("text", "").strip()
        voice = data.get("voice", KOKORO_VOICE_ENGLISH)

        if not text:
            return jsonify({"success": False, "error": "text is required"}), 400

        clipped  = text[:300]
        wav_data = kokoro_tts(clipped, voice=voice)
        if not wav_data:
            return jsonify({"success": False, "error": "Kokoro synthesis failed"}), 500

        voice_id = f"en_{int(time.time())}_{uuid.uuid4().hex}"
        sha256   = hashlib.sha256(wav_data).hexdigest()
        with open(os.path.join(VOICE_STORAGE_DIR, f"{voice_id}.wav"), "wb") as f:
            f.write(wav_data)

        print(f"[KOKORO] 完了: {voice_id} ({len(wav_data):,} bytes)")
        return jsonify({
            "success":       True,
            "voice_id":      voice_id,
            "size":          len(wav_data),
            "sha256":        sha256,
            "download_path": f"/voice/{voice_id}",
            "settings":      {"text": clipped, "voice": voice, "engine": "kokoro"},
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ── 日英混合TTS（VOICEVOX + Kokoro）────────────────────
@app.route("/generate_mixed", methods=["POST"])
def generate_mixed():
    try:
        _ensure_voice_storage_dir()
        data       = request.get_json(silent=True) or {}
        text       = data.get("text", "").strip()
        speaker_id = int(data.get("speaker_id", VOICEVOX_SPEAKER_ID))
        voice      = data.get("voice", KOKORO_VOICE_ENGLISH)

        if not text:
            return jsonify({"success": False, "error": "text is required"}), 400

        cached = check_cache(text)
        if cached:
            return jsonify(cached)

        clipped     = normalize_text(text[:300])
        segments    = split_text_by_lang(clipped)
        has_english = any(lang == 'en' for lang, _ in segments)

        if not has_english:
            print("[MIXED] 英語なし → VOICEVOXのみ")
            wav_data = generate_voicevox_wav(clipped, speaker_id)
        else:
            print(f"[MIXED] 日英混合 {len(segments)}セグメント")
            wav_parts = []
            for lang, seg in segments:
                print(f"  [{lang}] {seg[:40]}")
                wav = generate_voicevox_wav(seg, speaker_id) if lang == 'ja' else kokoro_tts(seg, voice=voice)
                if wav:
                    wav_parts.append(wav)
            if not wav_parts:
                return jsonify({"success": False, "error": "All synthesis failed"}), 500
            wav_data = concat_wav_bytes(wav_parts)

        if not wav_data:
            return jsonify({"success": False, "error": "Synthesis failed"}), 500

        voice_id = f"mix_{int(time.time())}_{uuid.uuid4().hex}"
        sha256   = hashlib.sha256(wav_data).hexdigest()
        with open(os.path.join(VOICE_STORAGE_DIR, f"{voice_id}.wav"), "wb") as f:
            f.write(wav_data)

        print(f"[MIXED] 完了: {voice_id} ({len(wav_data):,} bytes)")
        return jsonify({
            "success":       True,
            "voice_id":      voice_id,
            "size":          len(wav_data),
            "sha256":        sha256,
            "download_path": f"/voice/{voice_id}",
            "settings":      {"text": clipped, "engine": "mixed" if has_english else "voicevox"},
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ── ファイル配信系 ───────────────────────────────────────
@app.route("/voice/<voice_id>", methods=["GET"])
def get_voice(voice_id):
    vid = safe_voice_id(voice_id)
    if not vid:
        return jsonify({"success": False, "error": "invalid voice_id"}), 400

    if vid.startswith("cache_"):
        cache_key = vid.replace("cache_", "", 1)
        try:
            cache_path = get_cache_path(cache_key)
        except ValueError:
            return jsonify({"success": False, "error": "invalid cache key"}), 400
        if os.path.exists(cache_path):
            return send_file(cache_path, mimetype="audio/mpeg", as_attachment=True, download_name="voice.mp3")
        return jsonify({"success": False, "error": "voice not found"}), 404

    try:
        wav_path = resolve_under(VOICE_STORAGE_DIR, f"{vid}.wav")
    except ValueError:
        return jsonify({"success": False, "error": "invalid voice_id"}), 400

    if not wav_path.is_file():
        return jsonify({"success": False, "error": "voice not found"}), 404

    print(f"[📤] 音声配信: {vid}")
    return send_file(str(wav_path), mimetype="audio/wav", as_attachment=True, download_name="voice.wav")


@app.route("/song/<song_name>", methods=["GET"])
def get_song(song_name):
    name = safe_id(song_name)
    if not name:
        return jsonify({"success": False, "error": "invalid song_name"}), 400
    try:
        wav_path = resolve_under(SONGS_DIR, f"{name}.wav")
    except ValueError:
        return jsonify({"success": False, "error": "invalid song_name"}), 400
    if not wav_path.is_file():
        return jsonify({"success": False, "error": "song not found"}), 404
    print(f"[🎵] 曲配信: {name}")
    return send_file(str(wav_path), mimetype="audio/wav", as_attachment=True, download_name=f"{name}.wav")


@app.route("/speakers", methods=["GET"])
def list_speakers():
    try:
        r = requests.get(f"{VOICEVOX_URL}/speakers", timeout=5)
        r.raise_for_status()
        return jsonify({"success": True, "speakers": r.json()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/cleanup", methods=["POST"])
def cleanup():
    _ensure_voice_storage_dir()
    payload         = request.get_json(silent=True) or {}
    max_age_seconds = int(payload.get("max_age_seconds", 3600))
    keep_latest     = bool(payload.get("keep_latest", True))

    files = sorted(
        [(os.path.join(VOICE_STORAGE_DIR, f), os.path.getmtime(os.path.join(VOICE_STORAGE_DIR, f)))
         for f in os.listdir(VOICE_STORAGE_DIR) if f.endswith(".wav")],
        key=lambda x: x[1], reverse=True,
    )
    now, deleted = time.time(), 0
    for idx, (path, mtime) in enumerate(files):
        if keep_latest and idx == 0:
            continue
        if now - mtime > max_age_seconds:
            os.remove(path)
            deleted += 1

    if deleted:
        print(f"[🗑️] クリーンアップ: {deleted}件削除")
    return jsonify({"success": True, "deleted": deleted})


if __name__ == "__main__":
    print("=" * 50)
    print("🎤 音声生成サーバー (VOICEVOX + Kokoro)")
    print(f"📁 ストレージ: {VOICE_STORAGE_DIR}")
    print(f"🎵 VOICEVOX: {VOICEVOX_URL} / 話者: {VOICEVOX_SPEAKER_ID}")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5001, debug=False)
