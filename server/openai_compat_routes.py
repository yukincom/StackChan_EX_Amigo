"""OpenAI互換エンドポイント（StackChan向け）"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests as req
from flask import Blueprint, Response, jsonify, request

from config import config
from member_loader import MASTER_MASK_LABEL, mask_names
from memory_manager import RobotMemory
from services.chat_service import process_chat, resolve_speaker_context
from services.openai_function_service import (
    build_function_call_message,
    decide_function_call,
    render_personalized_function_result,
)
from services.speech_service import speech_service
from services.voice_service import generate_voice_mixed

openai_bp = Blueprint("openai", __name__)

# push 再生用 proxy の最大ダウンロードサイズ（WAV）
_PROXY_MAX_BYTES = 8 * 1024 * 1024
_PROXY_ALLOWED_PATH_PREFIXES = ("/voice/", "/song/")


def _proxy_allowed_netlocs() -> set[tuple[str, int]]:
    """(hostname_lower, port) の許可集合。voice_server と loopback のみ。"""
    allowed: set[tuple[str, int]] = set()

    def add(url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            return
        if parsed.port is not None:
            port = parsed.port
        elif parsed.scheme == "https":
            port = 443
        else:
            port = 80
        allowed.add((host, port))
        # IPv4 loopback 別名
        if host in ("127.0.0.1", "localhost"):
            allowed.add(("127.0.0.1", port))
            allowed.add(("localhost", port))

    add(config.VOICE_SERVER_URL)
    # デフォルト voice_server ポートも明示許可
    allowed.add(("127.0.0.1", 5001))
    allowed.add(("localhost", 5001))
    return allowed


def _validate_audio_proxy_src(src: str) -> str | None:
    """
    SSRF 防止: http のみ、host/port は voice_server 系、path は /voice/ or /song/。
    通れば正規化した src を返す。ダメなら None。
    """
    src = (src or "").strip()
    if not src or len(src) > 2048:
        return None
    try:
        parsed = urlparse(src)
    except Exception:
        return None
    if parsed.scheme != "http":
        return None
    if parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    port = parsed.port if parsed.port is not None else 80
    if (host, port) not in _proxy_allowed_netlocs():
        return None
    path = parsed.path or ""
    if not any(path.startswith(prefix) for prefix in _PROXY_ALLOWED_PATH_PREFIXES):
        return None
    # クエリは voice_server が使わないので拒否
    if parsed.query or parsed.fragment:
        return None
    return f"http://{host}:{port}{path}"


def _extract_text_content(content) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part)

    if isinstance(content, dict):
        return str(content.get("text", ""))

    return ""


def _normalize_messages(messages: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "")).strip()
        if role == "assistant":
            content = _extract_text_content(message.get("content")).strip()
            if not content:
                continue
        normalized.append(message)
    return normalized

def _build_stackchan_action(result: dict) -> dict | None:
    if not result.get("camera_triggered"):
        return None

    return {
        "type": "camera_trigger",
        "camera_mode": str(result.get("camera_mode", "transient")).strip() or "transient",
        "camera_requester": str(result.get("camera_requester", "user")).strip() or "user",
        "speaker": str(result.get("speaker", "master")).strip() or "master",
        "speaker_label": str(result.get("speaker_label", "")).strip(),
        "announce_endpoint": str(result.get("announce_endpoint", "")).strip(),
    }


def _wav_to_mp3(wav_data: bytes) -> bytes | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None

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
    except Exception as exc:
        print(f"[OpenAICompat] mp3 conversion failed: {exc}")
        return None
    finally:
        for path in (src_path, dst_path):
            if path and path.exists():
                path.unlink()


@openai_bp.route("/v1/models", methods=["GET"])
def list_models():
    model = config.AI_CHAT_MODEL or "stackchan-chat"
    now = int(time.time())
    return jsonify(
        {
            "object": "list",
            "data": [
                {
                    "id": model,
                    "object": "model",
                    "created": now,
                    "owned_by": "stackchan-server",
                }
            ],
        }
    )


@openai_bp.route("/audio/proxy.mp3", methods=["GET"])
def audio_proxy_mp3():
    """許可された voice_server URL の WAV を MP3 に変換して返す（push 再生向け）。"""
    try:
        raw_src = str(request.args.get("src", "")).strip()
        src = _validate_audio_proxy_src(raw_src)
        if not src:
            return jsonify({
                "error": {
                    "message": "src is not allowed (http voice_server /voice or /song only)",
                    "type": "invalid_request_error",
                }
            }), 400

        # stream + size cap（SSRF 後の大容量 DoS も抑制）
        with req.get(src, timeout=15, stream=True) as wav_response:
            wav_response.raise_for_status()
            content_length = wav_response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > _PROXY_MAX_BYTES:
                        return jsonify({
                            "error": {
                                "message": "audio too large",
                                "type": "invalid_request_error",
                            }
                        }), 400
                except ValueError:
                    pass

            chunks: list[bytes] = []
            total = 0
            for chunk in wav_response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > _PROXY_MAX_BYTES:
                    return jsonify({
                        "error": {
                            "message": "audio too large",
                            "type": "invalid_request_error",
                        }
                    }), 400
                chunks.append(chunk)
            wav_bytes = b"".join(chunks)

        mp3_data = _wav_to_mp3(wav_bytes)
        if not mp3_data:
            return jsonify({"error": {"message": "mp3 conversion failed", "type": "server_error"}}), 500

        return Response(mp3_data, mimetype="audio/mpeg")
    except Exception as exc:
        return jsonify({"error": {"message": str(exc), "type": "server_error"}}), 500


@openai_bp.route("/v1/audio/transcriptions", methods=["POST"])
def transcriptions():
    """STT: Whisper互換エンドポイント"""
    try:
        if "file" in request.files:
            audio_content = request.files["file"].read()
        else:
            audio_content = request.get_data(cache=False, parse_form_data=False)

        if not audio_content or len(audio_content) < config.SPEECH_MIN_BYTES:
            return jsonify({"error": {"message": "Audio too short", "type": "invalid_request_error"}}), 400

        language = request.form.get("language", "ja")
        transcript = speech_service.transcribe(audio_content, language_code=language)
        if not transcript:
            return jsonify({"error": {"message": "No speech detected", "type": "invalid_request_error"}}), 400

        return jsonify({"text": transcript})
    except Exception as exc:
        return jsonify({"error": {"message": str(exc), "type": "server_error"}}), 500


@openai_bp.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    """LLM: ChatGPT互換エンドポイント"""
    try:
        data = request.get_json(force=True, silent=False) or {}
        messages = _normalize_messages(data.get("messages", []))
        functions = data.get("functions", [])

        last_message = messages[-1] if messages else {}
        last_role = last_message.get("role")

        if last_role == "function":
            function_name = str(last_message.get("name", "")).strip()
            function_result = _extract_text_content(last_message.get("content"))
            user_text = ""
            for message in reversed(messages[:-1]):
                if message.get("role") == "user":
                    user_text = _extract_text_content(message.get("content"))
                    if user_text:
                        break
            response_text = render_personalized_function_result(function_name, function_result, user_text)
            speaker, speaker_label = resolve_speaker_context(user_text) if user_text else ("master", MASTER_MASK_LABEL)
            if user_text and response_text:
                memory = RobotMemory()
                memory.add_conversation(
                    speaker=speaker,
                    user_text=user_text,
                    ai_response=response_text,
                    speaker_label=speaker_label,
                )
            if response_text:
                print(f"[AI] ({len(response_text)}文字): {mask_names(response_text)}")
            model = data.get("model") or config.AI_CHAT_MODEL or "stackchan-chat"

            return jsonify(
                {
                    "id": f"chatcmpl-{uuid.uuid4().hex}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": response_text,
                            },
                        }
                    ],
                }
            )

        user_text = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                user_text = _extract_text_content(message.get("content"))
                if user_text:
                    break

        speaker, speaker_label = resolve_speaker_context(user_text) if user_text else ("master", MASTER_MASK_LABEL)

        decision = decide_function_call(user_text, functions)
        if decision is not None:
            model = data.get("model") or config.AI_CHAT_MODEL or "stackchan-chat"
            return jsonify(
                {
                    "id": f"chatcmpl-{uuid.uuid4().hex}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "function_call",
                            "message": build_function_call_message(decision),
                        }
                    ],
                }
            )

        result = process_chat(
            user_text,
            speaker=speaker,
            generate_voice_flag=False,
            speaker_label=speaker_label,
        )
        response_text = result.get("response") if result.get("success") else "わかりません"
        stackchan_action = _build_stackchan_action(result)
        model = data.get("model") or config.AI_CHAT_MODEL or "stackchan-chat"

        payload = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": response_text,
                    },
                }
            ],
        }
        if stackchan_action is not None:
            payload["stackchan_action"] = stackchan_action

        return jsonify(payload)
    except Exception as exc:
        return jsonify({"error": {"message": str(exc), "type": "server_error"}}), 500


@openai_bp.route("/v1/audio/speech", methods=["POST"])
def text_to_speech():
    """TTS: OpenAI TTS互換エンドポイント"""
    try:
        data = request.get_json(force=True, silent=False) or {}
        text = str(data.get("input", "")).strip()
        response_format = str(data.get("response_format", "wav")).lower()

        if not text:
            return jsonify({"error": {"message": "input is required", "type": "invalid_request_error"}}), 400

        result = generate_voice_mixed(text, speaker_id=config.VOICEVOX_SPEAKER_ID)
        if not result:
            return jsonify({"error": {"message": "TTS failed", "type": "server_error"}}), 500

        wav_data = req.get(result["source_url"], timeout=10).content

        if response_format in {"wav", "pcm"}:
            return Response(wav_data, mimetype="audio/wav")

        if response_format in {"mp3", "mpeg"}:
            mp3_data = _wav_to_mp3(wav_data)
            if not mp3_data:
                return jsonify({"error": {"message": "mp3 conversion failed", "type": "server_error"}}), 500
            return Response(mp3_data, mimetype="audio/mpeg")

        return jsonify(
            {
                "error": {
                    "message": f"unsupported response_format: {response_format}",
                    "type": "invalid_request_error",
                }
            }
        ), 400
    except Exception as exc:
        return jsonify({"error": {"message": str(exc), "type": "server_error"}}), 500
