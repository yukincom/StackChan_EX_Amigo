# services/notification_service.py
"""LINE/Discord通知サービスモジュール"""
from config import config
from member_loader import get_call_name_by_user_id, mask_names
from services.voice_service import generate_voice, push_to_m5stack
from memory_manager import RobotMemory


def generate_notification_message(sender_name, original_message):
    """元のメッセージ内容に基づいて通知メッセージを生成"""
    if "帰る" in original_message or "帰ります" in original_message:
        return f"{sender_name}がそろそろ帰ってくるって言っているよ！"
    elif "遅い" in original_message or "遅く" in original_message:
        return f"{sender_name}、今夜はちょっと遅いって言ってるよ〜"
    elif "よろしく" in original_message:
        return f"{sender_name}からよろしくって言ってるよ〜"
    elif "買" in original_message:
        return f"{sender_name}が買ってきてほしいものある？って聞いてるよ〜"
    else:
        return f"{sender_name}からメッセージだよ。「{original_message}」だってさ！"


def process_notification(user_id, message, *, delivery_key=None):
    """LINE/Discord通知を処理してM5Stackにpushする"""
    sender_name = get_call_name_by_user_id(user_id, default=config.FAMILY_DEFAULT)
    notification_message = generate_notification_message(sender_name, mask_names(message))

    # 音声生成 → M5Stackにpush
    voice_result = generate_voice(notification_message)
    delivery_accepted = bool(
        voice_result
        and push_to_m5stack(
            voice_result["source_url"],
            idempotency_key=delivery_key,
        )
    )
    if not delivery_accepted:
        print("[NOTIFY] 音声生成または配送受付に失敗")

    # 音声配送がローカル台帳へ受理された後だけメモリへ保存する。
    if delivery_accepted:
        memory = RobotMemory()
        memory.add_conversation(
            "notify",
            mask_names(message),
            "",
            speaker_label=f"[LINE通知] {sender_name}"
        )

    return {
        "success":       delivery_accepted,
        "sender":        sender_name,
        "message":       notification_message,
        "original_text": message,
    }
