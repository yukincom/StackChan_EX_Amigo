# app.py
"""Flaskアプリケーション"""


import json
import traceback
import threading
from datetime import datetime

import requests
from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import ClientDisconnected

from config import config
from ai_handler import is_english_mode
from llm_client import preload_mlx_model
from member_loader import has_line_users, has_discord_users
from services.chat_service import process_chat, resolve_speaker_context, trigger_stack_ai_camera_async
from services.discord_service import _init_last_message_id
from services.scheduler_service import line_delivery_configured, setup_scheduler, stop_scheduler
from services.batch_service import run_if_needed
from services.speech_service import speech_service
from services.voice_service import (
    ensure_voice_delivery_worker,
    voice_delivery_ready,
    warmup_stack_startup_message,
)
from services.vision_handler import handle_vision_upload
from services.vlm_service import preload_vml_model
from admin_routes import admin_bp
from openai_compat_routes import openai_bp
from observability import emit_trace, install_flask_tracing, trace_context
from operation_journal import JournalError, get_operation_journal

_RUNTIME_LOCK = threading.Lock()
_RUNTIME_STARTED = False
_JOURNAL_RECOVERED = False


def _recover_operation_journal() -> None:
    global _JOURNAL_RECOVERED
    if _JOURNAL_RECOVERED:
        return
    get_operation_journal().recover_interrupted()
    _JOURNAL_RECOVERED = True

def check_notification_config():
    """通知サービスの設定確認（起動時のみ）"""
    line_users = has_line_users()
    line_ok    = line_users and line_delivery_configured()
    discord_ok = has_discord_users()

    if not line_ok and not discord_ok:
        print("[NOTIFY] ⚠️ LINE relayまたはDiscordユーザーが未設定です")
        print("[NOTIFY] 通知機能はオフで起動します（会話機能は使えます）")
        return False

    if line_ok:
        print("[NOTIFY] ✅ LINE通知: 有効")
    else:
        reason = "relay未設定" if line_users else "ユーザーID未設定"
        print(f"[NOTIFY] LINE通知: {reason}のためオフ")

    if discord_ok:
        print("[NOTIFY] ✅ Discord通知: 有効")
    else:
        print("[NOTIFY] Discord通知: ユーザーID未設定のためオフ")

    return True

def start_runtime_services() -> bool:
    """通常運用向けのバックグラウンド処理を一度だけ開始する。

    Flask app の import/create から分離し、テストや診断ではモデル、通知、
    scheduler、M5Stack への起動音声を開始しない。
    """
    global _RUNTIME_STARTED
    with _RUNTIME_LOCK:
        if _RUNTIME_STARTED:
            return False
        try:
            _recover_operation_journal()
            _ensure_vision_worker()
            ensure_voice_delivery_worker()
            run_if_needed()
            if config.DISCORD_BOT_TOKEN:
                _init_last_message_id()
            notification_enabled = check_notification_config()
            # setup_scheduler() が scheduler.start() まで担当する。
            setup_scheduler(notification_enabled)
            preload_mlx_model()
            preload_vml_model()
            warmup_stack_startup_message()
        except Exception:
            # scheduler 開始後の model preload 等で失敗した場合も後始末する。
            stop_scheduler()
            raise
        _RUNTIME_STARTED = True
        return True


def stop_runtime_services() -> bool:
    """通常運用向けバックグラウンド処理を停止する。"""
    global _RUNTIME_STARTED
    with _RUNTIME_LOCK:
        if not _RUNTIME_STARTED:
            return False
        stop_scheduler()
        _RUNTIME_STARTED = False
        return True


def health_check():
    """ヘルスチェック"""
    return jsonify({
        "status": "ok",
        "version": "2.3",
        "memory": "local",
        "voice_server": config.VOICE_SERVER_URL,
        "runtime_started": _RUNTIME_STARTED,
    })

def get_server_time():
    """M5Stack起動時のserverHour同期用"""
    return jsonify({"server_hour": datetime.now().hour})

# 内部テスト用の入口。PC入力と音声入力を並行で試せるよう残している。
# 主導線は /v1/chat/completions。
def chat():
    """内部テスト用のチャットエンドポイント"""
    try:
        data = request.json
        user_text = data["text"]
        generate_voice_flag = data.get("generate_voice", False)

        speaker, speaker_label = resolve_speaker_context(user_text)
        print(f"[SPEAKER]{speaker}")

        result = process_chat(user_text, speaker, generate_voice_flag, speaker_label=speaker_label)

        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

def get_pending_notifications():
    """M5Stackからの時刻同期用（push型移行後はserverHourのみ返す）"""
    return jsonify({
        "success": True,
        "notification": None,
        "server_hour": datetime.now().hour,
    })

def get_voice_by_id(voice_id):
    """特定のvoice_idを取得（プロキシ）"""
    from path_safety import safe_voice_id

    vid = safe_voice_id(voice_id)
    if not vid:
        return jsonify({"success": False, "status": "invalid_voice_id"}), 400

    try:
        voice_url = f"{config.VOICE_SERVER_URL}/voice/{vid}"
        remote_response = requests.get(voice_url, timeout=30)

        if remote_response.status_code != 200:
            return jsonify({"success": False, "status": "not_found"}), 404

        return Response(
            remote_response.content,
            mimetype="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=voice.wav",
                "X-Voice-Id": vid,
            },
        )
    except requests.RequestException:
        return jsonify({"success": False, "status": "upstream_unreachable"}), 502


def transcribe_speech():
    """音声認識エンドポイント"""
    try:
        # 1回だけ読む（ここ重要）
        audio_content = request.get_data(cache=False, parse_form_data=False)
    except ClientDisconnected:
        return jsonify({"success": False, "error": "client_disconnected"}), 499

    try:
        if not audio_content or len(audio_content) < config.SPEECH_MIN_BYTES:
            return jsonify({"success": False, "error": "Audio too short"}), 400

        # 英語モデル切り替え
        use_english = is_english_mode()
        print(
            f"[STT] endpoint=/speech/transcribe "
            f"english_mode={use_english} "
            f"use_english_model_passed={use_english} "
            f"bytes={len(audio_content)}"
        )
        transcript = speech_service.transcribe(
            audio_content,
            use_english_model=use_english,
        )

        if not transcript:
            return jsonify({"success": False, "error": "No speech detected"}), 400

        return jsonify({"success": True, "transcript": transcript})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
    
# Vision: 無制限 Thread をやめ、単一ワーカー + キュー（満杯時は 429）
import queue as _queue
import threading as _threading
import time as _time

_VISION_QUEUE: _queue.Queue = _queue.Queue(maxsize=2)
_VISION_WORKER_STARTED = False
_VISION_WORKER_THREAD: _threading.Thread | None = None
_VISION_JOB_SEQ = 0
_VISION_JOB_LOCK = _threading.Lock()
_VISION_QUEUED_OPS: set[str] = set()


def _enqueue_persisted_vision() -> None:
    """Fill available RAM queue slots from durable retryable operations."""
    journal = get_operation_journal()
    for record in journal.pending("vision"):
        op_id = record["op_id"]
        with _VISION_JOB_LOCK:
            if op_id in _VISION_QUEUED_OPS:
                continue
            try:
                _VISION_QUEUE.put_nowait(op_id)
            except _queue.Full:
                return
            _VISION_QUEUED_OPS.add(op_id)


def _process_vision_operation(op_id: str) -> None:
    journal = get_operation_journal()
    journal.transition(op_id, "running", owner="worker", stage="processing")
    image_data = journal.load_payload(op_id, "image.jpg")
    try:
        request_data = json.loads(journal.load_payload(op_id, "request.json"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise JournalError("private vision request is invalid") from exc
    if not isinstance(request_data, dict):
        raise JournalError("private vision request is invalid")

    with trace_context(op_id):
        result = handle_vision_upload(
            image_data,
            str(request_data.get("requester", "user")),
            str(request_data.get("transcript", "")),
            str(request_data.get("speaker", "master")),
            str(request_data.get("speaker_label", "")),
            capture_mode=request_data.get("mode"),
        )
    if isinstance(result, dict) and result.get("success"):
        journal.transition(op_id, "succeeded", owner="worker", stage="complete")
        journal.discard_payloads(op_id)
        return
    journal.transition(op_id, "failed", owner="worker", stage="error")


def _vision_worker_loop():
    while True:
        op_id = _VISION_QUEUE.get()
        t0 = _time.monotonic()
        print(f"[VISION-Q] start op={op_id[:8]} qsize={_VISION_QUEUE.qsize()}")
        try:
            _process_vision_operation(op_id)
            print(f"[VISION-Q] done op={op_id[:8]} in {_time.monotonic() - t0:.1f}s")
        except Exception as exc:
            try:
                latest = get_operation_journal().latest().get(op_id)
                if latest and latest["state"] == "running":
                    get_operation_journal().transition(
                        op_id, "failed", owner="worker", stage="error"
                    )
            except JournalError:
                pass
            emit_trace("vision", "process", "error", trace_id=op_id, error_type=type(exc).__name__)
            traceback.print_exc()
            print(f"[VISION-Q] fail op={op_id[:8]} in {_time.monotonic() - t0:.1f}s")
        finally:
            with _VISION_JOB_LOCK:
                _VISION_QUEUED_OPS.discard(op_id)
            _VISION_QUEUE.task_done()
            try:
                _enqueue_persisted_vision()
            except JournalError:
                pass


def _ensure_vision_worker():
    global _VISION_WORKER_STARTED, _VISION_WORKER_THREAD
    _recover_operation_journal()
    with _VISION_JOB_LOCK:
        if (
            _VISION_WORKER_STARTED
            and _VISION_WORKER_THREAD is not None
            and _VISION_WORKER_THREAD.is_alive()
        ):
            return
        t = _threading.Thread(target=_vision_worker_loop, name="vision-worker", daemon=True)
        t.start()
        _VISION_WORKER_THREAD = t
        _VISION_WORKER_STARTED = True
    _enqueue_persisted_vision()
    print("[VISION-Q] worker started")


def vision_status():
    """診断用: キューと VLM の状態"""
    from services.vlm_service import vlm_status

    return jsonify({
        "queue_size": _VISION_QUEUE.qsize(),
        "queue_max": _VISION_QUEUE.maxsize,
        "worker_started": _VISION_WORKER_STARTED,
        "vlm": vlm_status(),
    })


def liveness_check():
    """Process-level probe. It intentionally does not imply runtime readiness."""
    return jsonify({"status": "alive", "version": "2.3"})


def readiness_check():
    journal_ready, _journal_reason = get_operation_journal().readiness()
    vision_ready = bool(
        _VISION_WORKER_STARTED
        and _VISION_WORKER_THREAD is not None
        and _VISION_WORKER_THREAD.is_alive()
    )
    checks = {
        "config": not bool(config.LOAD_ERRORS),
        "journal": journal_ready,
        "runtime": _RUNTIME_STARTED,
        "vision_worker": vision_ready,
        "voice_delivery_worker": voice_delivery_ready(),
    }
    ready = all(checks.values())
    return jsonify({"status": "ready" if ready else "not_ready", "checks": checks}), 200 if ready else 503


def vision_upload():
    """M5Stack からの画像受信 → VLM解析 → TTS push（非同期・キュー）"""
    global _VISION_JOB_SEQ
    try:
        try:
            image_data = request.get_data(cache=False, parse_form_data=False)
        except Exception:
            return jsonify({"success": False, "error": "client_disconnected"}), 499

        if not image_data or len(image_data) < 500:
            return jsonify({"success": False, "error": "Image too small"}), 400

        requester = request.args.get("r", "user")      # user or ai
        transcript = request.args.get("t", "")         # URL decoded 自動
        speaker = request.args.get("s", "master")
        speaker_label = request.args.get("sl", "")
        # Missing mode is a pre-upgrade firmware request. Preserve its legacy
        # keyword behavior; an explicit transient request must never archive.
        capture_mode = request.args.get("mode")
        if capture_mode is not None and capture_mode != "archive":
            capture_mode = "transient"

        with _VISION_JOB_LOCK:
            _VISION_JOB_SEQ += 1
            job_id = _VISION_JOB_SEQ
        private_request = json.dumps(
            {
                "job_id": job_id,
                "requester": requester,
                "transcript": transcript,
                "speaker": speaker,
                "speaker_label": speaker_label,
                "mode": capture_mode,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        journal = get_operation_journal()
        operation = journal.create(
            "vision",
            owner="api",
            payloads={"image.jpg": image_data, "request.json": private_request},
        )
        op_id = operation["op_id"]
        _ensure_vision_worker()
        try:
            with _VISION_JOB_LOCK:
                if op_id not in _VISION_QUEUED_OPS:
                    _VISION_QUEUE.put_nowait(op_id)
                    _VISION_QUEUED_OPS.add(op_id)
        except _queue.Full:
            journal.transition(op_id, "rejected", owner="api", stage="error")
            journal.discard_payloads(op_id)
            print(f"[VISION-Q] reject job={job_id} queue_full bytes={len(image_data)}")
            return jsonify({
                "success": False,
                "error": "vision_busy",
                "message": "Vision is processing; try again shortly",
            }), 429

        print(f"[VISION-Q] enqueued job={job_id} qsize={_VISION_QUEUE.qsize()} bytes={len(image_data)}")
        return jsonify({
            "success": True,
            "status": "processing",
            "job_id": job_id,
            "operation_id": op_id,
        })

    except JournalError:
        return jsonify({"success": False, "error": "journal_unavailable"}), 503
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


def vision_ai_trigger():
    """Mac側のAI判断でM5Stackのカメラを起動させる"""
    try:
        data = request.json or {}
        context = data.get("context", "")
        mode = data.get("mode", "transient")
        if trigger_stack_ai_camera_async(context, mode):
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "trigger_failed"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def register_core_routes(flask_app: Flask) -> None:
    """既存URLとendpoint名を保ったままcore routeを登録する。"""
    flask_app.add_url_rule("/health", "health_check", health_check, methods=["GET"])
    flask_app.add_url_rule("/health/live", "liveness_check", liveness_check, methods=["GET"])
    flask_app.add_url_rule("/health/ready", "readiness_check", readiness_check, methods=["GET"])
    flask_app.add_url_rule("/time", "get_server_time", get_server_time, methods=["GET"])
    flask_app.add_url_rule("/chat", "chat", chat, methods=["POST"])
    flask_app.add_url_rule(
        "/notify/pending",
        "get_pending_notifications",
        get_pending_notifications,
        methods=["GET"],
    )
    flask_app.add_url_rule(
        "/voice/<voice_id>",
        "get_voice_by_id",
        get_voice_by_id,
        methods=["GET"],
    )
    flask_app.add_url_rule(
        "/speech/transcribe",
        "transcribe_speech",
        transcribe_speech,
        methods=["POST"],
    )
    flask_app.add_url_rule("/vision/status", "vision_status", vision_status, methods=["GET"])
    flask_app.add_url_rule("/vision/upload", "vision_upload", vision_upload, methods=["POST"])
    flask_app.add_url_rule(
        "/vision/ai-trigger",
        "vision_ai_trigger",
        vision_ai_trigger,
        methods=["POST"],
    )


def create_app(test_config: dict | None = None) -> Flask:
    """副作用なしでFlask appを作成する。"""
    flask_app = Flask(__name__)
    if test_config:
        flask_app.config.update(test_config)
    flask_app.register_blueprint(admin_bp)
    flask_app.register_blueprint(openai_bp)
    register_core_routes(flask_app)
    install_flask_tracing(flask_app)
    return flask_app


# 従来の ``from app import app`` と ``python app.py`` を維持する。
app = create_app()


if __name__ == "__main__":
    try:
        start_runtime_services()
        app.run(host="0.0.0.0", port=config.SERVER_PORT, debug=False)
    finally:
        stop_runtime_services()
