#!/usr/bin/env python3
"""Import the real voice stack and exercise startup/WAV delivery on loopback."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import wave


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT)]


def run_smoke() -> dict:
    from flask import Flask, Response, jsonify
    import requests
    from tools.stackchan_lab.server_runner import LiveServer

    with tempfile.TemporaryDirectory(prefix="stackchan-voice-startup-") as name:
        root = Path(name)
        os.environ.update({
            "STACKCHAN_SKIP_DOTENV": "1",
            "VOICE_STORAGE_DIR": str(root / "voice"),
            "HF_HOME": str(root / "huggingface"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "XDG_CACHE_HOME": str(root / "cache"),
        })
        audio = io.BytesIO()
        with wave.open(audio, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(24000)
            writer.writeframes(b"\0\0" * 240)
        wav = audio.getvalue()
        fake = Flask("voicevox-startup-fixture")
        fake.add_url_rule("/version", endpoint="version", view_func=lambda: jsonify("fixture"))
        fake.add_url_rule("/audio_query", endpoint="audio_query", methods=["POST"], view_func=lambda: jsonify({"outputSamplingRate": 24000}))
        fake.add_url_rule("/synthesis", endpoint="synthesis", methods=["POST"], view_func=lambda: Response(wav, mimetype="audio/wav"))
        with LiveServer(fake) as backend:
            os.environ["VOICEVOX_URL"] = backend.base_url
            spec = importlib.util.spec_from_file_location("stackchan_voice_startup", ROOT / "server" / "voice_server.py")
            if spec is None or spec.loader is None:
                raise RuntimeError("voice server loader unavailable")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            from voice_server import tts_kokoro
            from importlib.metadata import version
            checks = {"real_voice_dependencies_import": all(version(p) for p in ("numpy", "soundfile", "kokoro"))}
            with LiveServer(module.app) as server:
                health = requests.get(server.base_url + "/health", timeout=10)
                checks["voice_server_http_startup"] = health.status_code == 200 and health.json()["voicevox_status"] == "ok"
                generated = requests.post(server.base_url + "/generate", json={"text": "起動確認です。"}, timeout=10)
                checks["japanese_generation_contract"] = generated.status_code == 200 and generated.json().get("success") is True
                if not checks["japanese_generation_contract"]:
                    raise RuntimeError("Japanese voice generation failed")
                result = generated.json()
                delivered = requests.get(server.base_url + result["download_path"], timeout=10)
                checks["generated_wav_delivery"] = delivered.status_code == 200 and delivered.content == wav
                checks["kokoro_model_not_loaded"] = tts_kokoro._kokoro_pipeline is None
            if not all(checks.values()):
                raise RuntimeError(f"voice startup checks failed: {checks}")
            return {"status": "ok", "checks": checks}


if __name__ == "__main__":
    print(json.dumps(run_smoke(), ensure_ascii=False, indent=2))
