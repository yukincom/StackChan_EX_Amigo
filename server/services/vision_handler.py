"""
services/vision_handler.py
M5Stack → 画像受信 → VLM解析 → チャット応答 → TTS push
"""
import logging
from datetime import datetime
from pathlib import Path
import os
import json
import shutil

from config import config
from services.vlm_service import analyze_image
from services.voice_service import generate_voice_mixed, push_to_m5stack
from ai_handler import get_ai_response
from memory_manager import RobotMemory
from member_loader import MASTER_MASK_LABEL, mask_names

logger = logging.getLogger(__name__)

# 画像の一時保存先（処理後に削除）
VISION_TMP_DIR = Path(os.getenv("VISION_DIR", "/tmp/stackchan_vision"))
VISION_TMP_DIR.mkdir(parents=True, exist_ok=True)

# Vision応答の上限（1〜2文目安）
MAX_VISION_REPLY_CHARS = 120
VISION_USER_ERROR_MESSAGE = "ごめんね、今うまく見えなかった。もう一度見せてくれる？"


def _contains_any_keyword(text: str, keywords: list[str]) -> bool:
    normalized = (text or "").strip()
    normalized_lower = normalized.lower()
    return any(
        keyword and ((keyword in normalized) or (keyword.lower() in normalized_lower))
        for keyword in keywords
    )


def _normalize_vlm_desc_for_chat(vlm_desc: str, assistant_name: str) -> str:
    text = (vlm_desc or "").strip()
    if not text:
        return ""

    ai_name = (assistant_name or "").strip() or "この子"
    replacements = (
        ("鏡に映る自分", f"鏡に映る{ai_name}"),
        ("鏡に映った自分", f"鏡に映る{ai_name}"),
        ("鏡に映っている自分", f"鏡に映っている{ai_name}"),
        ("鏡の中の自分", f"鏡の中の{ai_name}"),
        ("自分の手", f"{ai_name}の手"),
        ("自分", ai_name),
    )
    for src, dst in replacements:
        text = text.replace(src, dst)
    return text


def _archive_vision_capture(
    source_path: Path,
    ts: datetime,
    requester: str,
    transcript: str,
    vlm_desc: str,
    ai_response: str,
) -> Path:
    archive_dir = Path(config.VISION_ARCHIVE_DIR).expanduser()
    archive_dir.mkdir(parents=True, exist_ok=True)

    stem = f"vision_{ts.strftime('%Y%m%d_%H%M%S_%f')[:19]}"
    image_path = archive_dir / f"{stem}.jpg"
    meta_path = archive_dir / f"{stem}.json"

    shutil.copy2(source_path, image_path)
    meta_path.write_text(
        json.dumps(
            {
                "timestamp": ts.isoformat(),
                "requester": requester,
                "transcript": transcript,
                "vlm_desc": vlm_desc,
                "ai_response": ai_response,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("[VISION] Archived image: %s", image_path)
    return image_path


def _record_vision_failure(memory: RobotMemory, label: str, transcript: str, reason: str) -> None:
    memory.add_conversation(
        speaker="vision",
        user_text=f"📷 {label}（{transcript}）",
        ai_response=f"（vision失敗: {reason}）",
        speaker_label="Vision失敗",
    )


def _push_vision_error_message(message: str) -> bool:
    voice_result = generate_voice_mixed(message)
    if not voice_result or not voice_result.get("source_url"):
        logger.warning("[VISION] failed to generate fallback error voice")
        return False
    return push_to_m5stack(voice_result["source_url"], fire_and_forget=False)


def handle_vision_upload(
    image_data: bytes,
    requester: str,     # "user" or "ai"
    transcript: str,    # 発話テキスト（"見て！"など）
    speaker: str = "master",
    speaker_label: str = "",
) -> dict:
    """
    M5Stackから受信した画像を処理してTTS応答を生成。
    非同期で呼ばれることを前提（Flask endpointから threading.Thread で実行）。
    """
    ts = datetime.now()
    resolved_master_label = MASTER_MASK_LABEL
    resolved_speaker = (speaker or "master").strip() or "master"
    resolved_speaker_label = (speaker_label or "").strip() or resolved_master_label
    safe_speaker_label = mask_names(resolved_speaker_label)
    request_label = f"{safe_speaker_label}撮影依頼" if requester == "user" else "AI撮影依頼"

    # 一時ファイルに保存
    tmp_path = VISION_TMP_DIR / f"cap_{ts.strftime('%Y%m%d_%H%M%S_%f')[:19]}.jpg"

    try:
        tmp_path.write_bytes(image_data)

        print(f"[VISION] Saved: {tmp_path} ({len(image_data)} bytes)")
        print(
            f"[VISION] requester={requester} speaker={resolved_speaker} "
            f"transcript={mask_names(transcript)[:60]} bytes={len(image_data)}"
        )

        # today.md に撮影タイムスタンプを記録
        memory = RobotMemory()
        memory.add_conversation(
            speaker="vision",
            user_text=f"📷 {request_label}（{transcript}）",
            ai_response="",
            speaker_label=request_label,
        )

        # ── VLM 画像解析 ────────────────────────────
        vlm_desc = analyze_image(str(tmp_path), transcript)

        if not vlm_desc:
            logger.warning("[VISION] VLM analysis failed")
            pushed = _push_vision_error_message(VISION_USER_ERROR_MESSAGE)
            logger.info("[VISION] fallback_error_voice pushed=%s", pushed)
            _record_vision_failure(memory, request_label, transcript, "VLM analysis failed")
            return {"success": False, "error": "VLM analysis failed", "user_message": VISION_USER_ERROR_MESSAGE}

        # ── チャット応答生成 ─────────────────────────
        assistant_name = (config.ASSISTANT_NAME or "").strip() or "この子"
        chat_vlm_desc = _normalize_vlm_desc_for_chat(vlm_desc, assistant_name)
        augmented = (
            f"ユーザーの発話: {transcript}\n"
            f"画像解析結果: {chat_vlm_desc}\n"
            f"画像解析結果に含まれる「自分」は、あなた自身（{assistant_name}）を指します。"
            "あなたはすでに画像を見終わっています。"
            "「見せて」「見てみる」「一緒に見てみよう」「何だろう」など、"
            "これから追加で確認するような表現は使わないでください。"
            "画像解析結果だけをもとに、今見えているものを自然な日本語で1〜2文、合計120文字以内で伝えてください。"
            "解析結果だけでは特定できない物は、無理に断定せず、見えている範囲だけを説明してください。"
        )
        context = memory.get_context(query=vlm_desc)

        ai_response = get_ai_response(
            user_text=augmented,
            context=context,
            speaker=resolved_speaker,
            mode="normal",
        )

        if not ai_response:
            ai_response = "うまく言葉にできなかったよ。もう一度見せてくれる？"

        ai_response = ai_response.strip()
        if len(ai_response) > MAX_VISION_REPLY_CHARS:
            ai_response = ai_response[:MAX_VISION_REPLY_CHARS] + "…"


        print(f"[VISION] vlm_desc({len(vlm_desc or '')}文字): {mask_names((vlm_desc or '')[:80])}")
        print(f"[AI-VISION] ({len(ai_response)}文字): {mask_names(ai_response)}")

        archive_requested = _contains_any_keyword(transcript, config.VISION_ARCHIVE_KEYWORDS)
        transient_requested = _contains_any_keyword(transcript, config.VISION_TRANSIENT_KEYWORDS)

        if archive_requested:
            archived_path = _archive_vision_capture(
                source_path=tmp_path,
                ts=ts,
                requester=requester,
                transcript=transcript,
                vlm_desc=vlm_desc,
                ai_response=ai_response,
            )
            memory.add_conversation(
                speaker="vision",
                user_text=f"📁 資料保存: {archived_path.name}",
                ai_response="",
                speaker_label="資料保存",
            )
        elif transient_requested:
            logger.info("[VISION] transient capture handled and will be deleted: %s", tmp_path.name)

        # today.md に会話ログ記録
        memory.add_conversation(
            speaker="vision",
            user_text=mask_names(f"📷 {transcript} → {vlm_desc}"),
            ai_response=mask_names(ai_response),
            speaker_label=safe_speaker_label,
        )

        # ── TTS 生成 → M5Stack push ──────────────────
        voice_result = generate_voice_mixed(ai_response)
        if voice_result and voice_result.get("source_url"):
            voice_url = voice_result["source_url"]
            pushed = push_to_m5stack(voice_url, fire_and_forget=False)
            logger.info("[VISION] voice_url=%s pushed=%s", voice_url, pushed)
            if not pushed:
                logger.warning("[VISION] push failed after voice generation")
                _record_vision_failure(memory, request_label, transcript, "push failed")
                return {"success": False, "error": "push failed", "user_message": VISION_USER_ERROR_MESSAGE}
        else:
            logger.warning("[VISION] voice generation failed (empty source_url)")
            pushed = _push_vision_error_message(VISION_USER_ERROR_MESSAGE)
            logger.info("[VISION] fallback_error_voice pushed=%s", pushed)
            _record_vision_failure(memory, request_label, transcript, "voice generation failed")
            return {"success": False, "error": "voice generation failed", "user_message": VISION_USER_ERROR_MESSAGE}

        return {"success": True, "response": ai_response}

    except Exception as e:
        logger.error(
            "[VISION] Unexpected error requester=%s transcript=%s err=%s",
            requester,
            (transcript or "")[:120],
            e,
            exc_info=True,
        )
        try:
            _push_vision_error_message(VISION_USER_ERROR_MESSAGE)
        except Exception:
            logger.exception("[VISION] fallback push also failed")
        return {"success": False, "error": str(e), "user_message": VISION_USER_ERROR_MESSAGE}

    finally:
        # 画像を必ず削除（成功・失敗問わず）
        if tmp_path.exists():
            tmp_path.unlink()
            logger.info("[VISION] Image deleted: %s", tmp_path)
