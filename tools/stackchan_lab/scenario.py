"""Self-contained no-device smoke scenario through the production Flask app."""

from __future__ import annotations

import os
import queue
import shutil
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse

import requests

from .fake_services import FakeServicesState, create_fake_services_app
from .firmware_client import FirmwareClient
from .server_runner import LiveServer
from .virtual_m5 import VirtualM5State, create_virtual_m5_app


def _server_modules(repo_root: Path):
    """Import the production server with real dotenv and runtime disabled."""
    server_root = (repo_root / "server").resolve()
    if not (server_root / "app.py").is_file():
        raise RuntimeError(f"server/app.py not found under {repo_root}")
    server_path = str(server_root)
    if server_path in sys.path:
        sys.path.remove(server_path)
    sys.path.insert(0, server_path)

    import app as app_module
    import memory_manager
    import openai_compat_routes
    from services import voice_service

    if Path(app_module.__file__).resolve().parent != server_root:
        raise RuntimeError("an unrelated module named 'app' is already imported")

    return app_module, memory_manager, openai_compat_routes, voice_service


def _audio_fetch_ok(events: list[dict[str, Any]]) -> bool:
    return any(
        event.get("event") == "audio_fetched"
        and event.get("status") == 200
        and int(event.get("bytes", 0)) > 0
        for event in events
    )


def run_smoke_scenario(repo_root: Path | None = None) -> dict[str, Any]:
    repo_root = (repo_root or Path.cwd()).expanduser().resolve()
    checks: dict[str, bool] = {}
    known_gaps: dict[str, bool] = {}

    with tempfile.TemporaryDirectory(prefix="stackchan-lab-smoke-") as temp_name:
        temp_root = Path(temp_name)
        json_dir = temp_root / "server" / "json"
        shutil.copytree(repo_root / "server" / "json_example", json_dir)
        safe_env = {
            "STACKCHAN_SKIP_DOTENV": "1",
            "STACKCHAN_ENV_DIR": str(temp_root / "env"),
            "STACKCHAN_JSON_DIR": str(json_dir),
            "MEMORY_DIR": str(temp_root / "server" / "memory"),
            "ARCHIVE_DIR": str(temp_root / "server" / "archive"),
            "VOICE_STORAGE_DIR": str(temp_root / "server" / "voice_store"),
            "VISION_DIR": str(temp_root / "vision"),
            "VISION_ARCHIVE_DIR": str(temp_root / "vision-archive"),
            "AI_PROVIDER": "lab",
            "AI_SUMMARY_PROVIDER": "lab",
            "ADMIN_TOKEN": "",
        }
        with patch.dict(os.environ, safe_env, clear=False):
            app_module, memory_manager, openai_routes, voice_service = _server_modules(repo_root)
            fake_state = FakeServicesState()
            vision_queue: queue.Queue = queue.Queue(maxsize=2)

            with LiveServer(create_fake_services_app(fake_state)) as fake_server:
                with ExitStack() as patches:
                    patches.enter_context(patch.object(app_module.config, "SPEECH_MIN_BYTES", 8))
                    patches.enter_context(
                        patch.object(memory_manager, "MEMORY_DIR", temp_root / "server" / "memory")
                    )
                    patches.enter_context(patch.object(openai_routes.config, "SPEECH_MIN_BYTES", 8))
                    patches.enter_context(patch.object(openai_routes.config, "AI_PROVIDER", "lab"))
                    patches.enter_context(patch.object(openai_routes.config, "AI_SUMMARY_PROVIDER", "lab"))
                    patches.enter_context(patch.object(openai_routes.config, "VOICE_SERVER_URL", fake_server.base_url))
                    patches.enter_context(patch.object(voice_service.config, "VOICE_SERVER_URL", fake_server.base_url))
                    patches.enter_context(
                        patch.object(openai_routes.speech_service, "transcribe", return_value=fake_state.transcript)
                    )
                    patches.enter_context(patch.object(openai_routes, "_wav_to_mp3", return_value=fake_state.mp3_bytes))
                    patches.enter_context(patch.object(app_module, "_VISION_QUEUE", vision_queue))
                    patches.enter_context(patch.object(app_module, "_VISION_WORKER_STARTED", False))
                    patches.enter_context(patch.object(app_module, "_VISION_JOB_SEQ", 0))
                    patches.enter_context(patch.object(app_module, "_ensure_vision_worker", lambda: None))

                    production_app = app_module.create_app({"TESTING": True})
                    with LiveServer(production_app) as production_server:
                        device_state = VirtualM5State(callback_server_url=production_server.base_url)
                        with LiveServer(create_virtual_m5_app(device_state)) as device_server:
                            device_address = urlparse(device_server.base_url)
                            with patch.object(voice_service.config, "M5STACK_IP", device_address.hostname):
                                with patch.object(voice_service.config, "M5STACK_PORT", device_address.port):
                                    firmware = FirmwareClient(production_server.base_url)

                                    health = requests.get(f"{production_server.base_url}/health", timeout=3)
                                    checks["production_app_runtime_stays_stopped"] = (
                                        health.status_code == 200
                                        and health.json().get("runtime_started") is False
                                    )

                                    stt = firmware.transcribe(b"RIFF" + b"\x00" * 512)
                                    checks["production_stt_contract"] = (
                                        stt.status_code == 200
                                        and stt.json().get("text") == fake_state.transcript
                                    )

                                    chat = firmware.chat([{"role": "user", "content": "こんにちは"}])
                                    chat_payload = chat.json()
                                    checks["production_chat_contract"] = (
                                        chat.status_code == 200
                                        and chat_payload.get("choices", [{}])[0]
                                        .get("message", {})
                                        .get("content")
                                        == "仮想環境の固定応答です。"
                                    )

                                    timer_function = {
                                        "name": "timer",
                                        "description": "set a timer",
                                        "parameters": {
                                            "type": "object",
                                            "properties": {"time": {"type": "integer"}},
                                        },
                                    }
                                    function_first = firmware.chat(
                                        [{"role": "user", "content": "1分タイマーをセットして"}],
                                        functions=[timer_function],
                                    )
                                    first_message = (
                                        function_first.json().get("choices", [{}])[0].get("message", {})
                                    )
                                    function_second = firmware.chat(
                                        [
                                            {"role": "user", "content": "1分タイマーをセットして"},
                                            {
                                                "role": "function",
                                                "name": "timer",
                                                "content": "1分のタイマーを設定したよ。",
                                            },
                                        ],
                                        functions=[timer_function],
                                    )
                                    second_message = (
                                        function_second.json().get("choices", [{}])[0].get("message", {})
                                    )
                                    checks["production_function_call_two_round_trips"] = (
                                        function_first.status_code == 200
                                        and first_message.get("function_call", {}).get("name") == "timer"
                                        and function_second.status_code == 200
                                        and second_message.get("content") == "1分のタイマーを設定したよ。"
                                    )

                                    speech = firmware.speech("テスト")
                                    checks["production_tts_contract"] = (
                                        speech.status_code == 200
                                        and speech.headers.get("Content-Type", "").startswith("audio/mpeg")
                                        and speech.content == fake_state.mp3_bytes
                                    )

                                    push_ok = voice_service.push_to_m5stack(
                                        f"{fake_server.base_url}/voice/cache_lab.mp3",
                                        fire_and_forget=False,
                                    )
                                    first_advance = requests.post(
                                        f"{device_server.base_url}/__lab__/advance",
                                        timeout=3,
                                    )
                                    second_advance = requests.post(
                                        f"{device_server.base_url}/__lab__/advance",
                                        timeout=3,
                                    )
                                    checks["production_push_reaches_virtual_device"] = (
                                        push_ok
                                        and first_advance.status_code == 200
                                        and second_advance.status_code == 200
                                        and device_state.mode == "idle"
                                    )
                                    checks["virtual_device_verified_audio_fetch"] = _audio_fetch_ok(
                                        device_state.events
                                    )

                                    camera = requests.post(
                                        f"{device_server.base_url}/camera/trigger",
                                        json={
                                            "requester": "user",
                                            "context": "見て",
                                            "mode": "transient",
                                            "speaker": "master",
                                        },
                                        timeout=3,
                                    )
                                    camera_advance = requests.post(
                                        f"{device_server.base_url}/__lab__/advance",
                                        timeout=3,
                                    )
                                    queued_vision = vision_queue.get_nowait() if not vision_queue.empty() else None
                                    checks["virtual_camera_reaches_production_server"] = (
                                        camera.status_code == 200
                                        and camera_advance.status_code == 200
                                        and queued_vision is not None
                                        and len(queued_vision[1]) > 500
                                        and queued_vision[2:5] == ("user", "見て", "master")
                                    )
                                    # Diagnostic metadata only: this must never make a check green.
                                    known_gaps["camera_mode_not_transmitted_by_current_firmware"] = (
                                        device_state.camera_upload_v2 is False
                                        and queued_vision is not None
                                        and len(queued_vision) == 6
                                    )

            required_ok = bool(checks) and all(checks.values())
            report = {
                "status": (
                    "error"
                    if not required_ok
                    else "degraded"
                    if any(known_gaps.values())
                    else "ok"
                ),
                "checks": checks,
                "known_gaps": known_gaps,
                "production_vision": {
                    "received": queued_vision is not None,
                    "bytes": len(queued_vision[1]) if queued_vision else 0,
                    "requester": queued_vision[2] if queued_vision else "",
                },
                "device_events": device_state.events,
                "fake_service_events": fake_state.events,
            }
    return report
