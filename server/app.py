# app.py
"""Flaskアプリケーション"""


import traceback
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
from services.scheduler_service import setup_scheduler, start_scheduler, stop_scheduler
from services.batch_service import run_if_needed
from services.speech_service import speech_service
from services.voice_service import warmup_stack_startup_message
from services.vision_handler import handle_vision_upload
from services.vlm_service import preload_vml_model
from admin_routes import admin_bp
from openai_compat_routes import openai_bp


app = Flask(__name__)
app.register_blueprint(admin_bp)
app.register_blueprint(openai_bp)

def check_notification_config():
    """通知サービスの設定確認（起動時のみ）"""
    line_ok    = has_line_users()
    discord_ok = has_discord_users()

    if not line_ok and not discord_ok:
        print("[NOTIFY] ⚠️ LINE・Discord ともにユーザーIDが未設定です")
        print("[NOTIFY] 通知機能はオフで起動します（会話機能は使えます）")
        return False

    if line_ok:
        print("[NOTIFY] ✅ LINE通知: 有効")
    else:
        print("[NOTIFY] LINE通知: ユーザーID未設定のためオフ")

    if discord_ok:
        print("[NOTIFY] ✅ Discord通知: 有効")
    else:
        print("[NOTIFY] Discord通知: ユーザーID未設定のためオフ")

    return True

# スケジューラー開始（インポート時に自動開始）
run_if_needed()
if config.DISCORD_BOT_TOKEN:
    _init_last_message_id()
notification_enabled = check_notification_config()  
setup_scheduler(notification_enabled)               
start_scheduler()
preload_mlx_model()
preload_vml_model()  
warmup_stack_startup_message()

@app.route("/health", methods=["GET"])
def health_check():
    """ヘルスチェック"""
    return jsonify({
        "status": "ok",
        "version": "2.3",
        "memory": "local",
        "voice_server": config.VOICE_SERVER_URL,
    })

@app.route("/time", methods=["GET"])
def get_server_time():
    """M5Stack起動時のserverHour同期用"""
    return jsonify({"server_hour": datetime.now().hour})

# 内部テスト用の入口。PC入力と音声入力を並行で試せるよう残している。
# 主導線は /v1/chat/completions。
@app.route("/chat", methods=["POST"])
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

@app.route("/notify/pending", methods=["GET"])
def get_pending_notifications():
    """M5Stackからの時刻同期用（push型移行後はserverHourのみ返す）"""
    return jsonify({
        "success": True,
        "notification": None,
        "server_hour": datetime.now().hour,
    })

@app.route("/voice/<voice_id>", methods=["GET"])
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


@app.route("/speech/transcribe", methods=["POST"])
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

_VISION_QUEUE: _queue.Queue = _queue.Queue(maxsize=2)
_VISION_WORKER_STARTED = False


def _vision_worker_loop():
    while True:
        item = _VISION_QUEUE.get()
        try:
            handle_vision_upload(*item)
        except Exception:
            traceback.print_exc()
        finally:
            _VISION_QUEUE.task_done()


def _ensure_vision_worker():
    global _VISION_WORKER_STARTED
    if _VISION_WORKER_STARTED:
        return
    t = _threading.Thread(target=_vision_worker_loop, name="vision-worker", daemon=True)
    t.start()
    _VISION_WORKER_STARTED = True


@app.route("/vision/upload", methods=["POST"])
def vision_upload():
    """M5Stack からの画像受信 → VLM解析 → TTS push（非同期・キュー）"""
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

        _ensure_vision_worker()
        try:
            _VISION_QUEUE.put_nowait(
                (image_data, requester, transcript, speaker, speaker_label)
            )
        except _queue.Full:
            return jsonify({
                "success": False,
                "error": "vision_busy",
                "message": "Vision is processing; try again shortly",
            }), 429

        return jsonify({"success": True, "status": "processing"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/vision/ai-trigger", methods=["POST"])
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
    
if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=config.SERVER_PORT, debug=False)
    finally:
        # アプリケーション終了時にスケジューラーを停止
        stop_scheduler()
