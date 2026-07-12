# services/chat_service.py
"""チャットサービスモジュール"""

import traceback
import threading
import time

import requests

from config import config
from ai_handler import (
    detect_intent,
    get_ai_response,
    is_english_mode,
    needs_search,
    set_english_mode,
)
from services.voice_cache_catalog import get_pc_cache_text
from services.voice_service import generate_voice_mixed, push_to_m5stack
from services.weather_service import get_weather_response
from member_loader import (
    MASTER_MASK_LABEL,
    get_all_names,
    get_primary_name,
    get_speaker_mask_label_map,
    get_speaker_patterns,
    load_member_data,
    mask_names,
)
from memory_manager import RobotMemory


def _play_stack_local(endpoint: str) -> bool:
    safe = (endpoint or "").strip()
    if not safe:
        return False

    try:
        url = f"http://{config.M5STACK_IP}:{config.M5STACK_PORT}/play_local"
        response = requests.post(
            url,
            json={"endpoint": safe},
            timeout=max(config.M5STACK_TIMEOUT, 10),
        )
        print(f"[PLAY-LOCAL] endpoint={safe} -> {response.status_code}")
        return response.ok
    except Exception as exc:
        print(f"[PLAY-LOCAL] failed endpoint={safe}: {exc}")
        return False


def _trigger_stack_camera_async(
    user_text: str,
    camera_mode: str,
    requester: str = "user",
    announce_endpoint: str = "",
) -> bool:
    """StackChan の camera trigger を非同期で叩く。

    StackChan 自身の /v1/chat/completions リクエスト処理中に同期で呼ぶと、
    Web サーバーが handleClient に戻る前に待ちが発生しやすい。
    そのため少し遅らせて別スレッドで送る。
    """

    def _worker():
        try:
            time.sleep(0.2)
            if announce_endpoint:
                _play_stack_local(announce_endpoint)
            url = f"http://{config.M5STACK_IP}:{config.M5STACK_PORT}/camera/trigger"
            response = requests.post(
                url,
                json={"requester": requester, "context": user_text, "mode": camera_mode},
                timeout=max(config.M5STACK_TIMEOUT, 30),
            )
            print(f"[VISION] camera trigger -> {response.status_code}")
        except Exception as exc:
            print(f"[VISION] camera trigger failed: {exc}")

    try:
        threading.Thread(target=_worker, daemon=True).start()
        return True
    except Exception as exc:
        print(f"[VISION] failed to start trigger thread: {exc}")
        return False


def trigger_stack_ai_camera_async(context: str, camera_mode: str = "transient") -> bool:
    return _trigger_stack_camera_async(
        context,
        camera_mode,
        requester="ai",
        announce_endpoint=config.AI_VISION_TRIGGER_ENDPOINT,
    )


def _detect_camera_mode(user_text: str) -> str | None:
    normalized = (user_text or "").strip()
    normalized_lower = normalized.lower()

    def _matches(keyword: str) -> bool:
        kw = (keyword or "").strip()
        if not kw:
            return False
        return kw in normalized or kw.lower() in normalized_lower

    if any(_matches(kw) for kw in config.VISION_ARCHIVE_KEYWORDS):
        return "archive"
    if any(_matches(kw) for kw in config.VISION_TRANSIENT_KEYWORDS):
        return "transient"
    return None


def _extract_ai_camera_mode(ai_response: str) -> tuple[str, str | None]:
    text = (ai_response or "").strip()
    if not text:
        return "", None

    archive_marker = config.AI_VISION_ARCHIVE_MARKER.strip()
    transient_marker = config.AI_VISION_TRANSIENT_MARKER.strip()

    if archive_marker and archive_marker in text:
        return text.replace(archive_marker, "").strip(), "archive"
    if transient_marker and transient_marker in text:
        return text.replace(transient_marker, "").strip(), "transient"
    return text, None


def _camera_trigger_marker(camera_mode: str) -> str:
    if camera_mode == "archive":
        return config.AI_VISION_ARCHIVE_MARKER.strip()
    return config.AI_VISION_TRANSIENT_MARKER.strip()


def _detect_weather_target(user_text: str) -> str | None:
    text = (user_text or "").strip()
    lower_text = text.lower()
    if not text:
        return None

    if any(kw in text for kw in config.WEATHER_KEYWORDS_TOMORROW):
        return "tomorrow"
    if any(kw in text for kw in config.WEATHER_KEYWORDS_TODAY):
        return "today"

    has_weather_word = (
        "天気" in text
        or "てんき" in text
        or "天気予報" in text
        or "weather" in lower_text
        or "forecast" in lower_text
    )
    if not has_weather_word:
        return None

    if any(token in text for token in ["明日", "あした", "あす"]) or "tomorrow" in lower_text:
        return "tomorrow"
    return "today"


def _recent_turns_for_ai_context(user_text: str) -> int:
    if not needs_search(user_text):
        return config.AI_RECENT_TURNS

    # 検索時の設定値は「今回の入力」を含む総ターン数として扱う。
    total_turns = max(1, config.AI_SEARCH_RECENT_TURNS)
    return max(total_turns - 1, 0)


def _camera_trigger_result(
    camera_mode: str,
    requester: str,
    speaker: str,
    speaker_label: str,
    announce_endpoint: str = "",
) -> dict:
    return {
        "success": True,
        "response": _camera_trigger_marker(camera_mode),
        "error": None,
        "camera_triggered": True,
        "camera_mode": camera_mode,
        "camera_requester": requester,
        "speaker": speaker,
        "speaker_label": speaker_label,
        "announce_endpoint": (announce_endpoint or "").strip(),
    }


def _text_has_now_scene_token(text: str) -> bool:
    """「今/いま」判定。「今日」に含まれる「今」は誤爆するので除く。"""
    cleaned = (
        (text or "")
        .replace("今日", "")
        .replace("きょう", "")
        .replace("本日", "")
    )
    return "今" in cleaned or "いま" in cleaned


def _looks_like_visual_question(user_text: str) -> bool:
    normalized = (user_text or "").strip()
    normalized_lower = normalized.lower()
    if not normalized:
        return False

    # 天気質問は視覚ヒューリスティックの対象外（「今日の天気は何？」誤爆防止）
    if _detect_weather_target(normalized):
        return False

    jp_demonstratives = ("これ", "それ", "あれ")
    en_demonstratives = ("this", "that", "it")
    # 「今」は _text_has_now_scene_token で別判定（今日の部分一致を避ける）
    jp_scene_targets = ("前に", "まえに", "目の前", "めのまえ", "周り", "まわり")
    jp_visual_topics = (
        "何", "なん", "何だ", "なんだ", "何だろう", "なんだろう",
        "知ってる", "同じ", "見える", "何に使う", "なんにつかう",
    )
    en_visual_topics = ("what", "same", "see", "use", "used for")
    jp_scene_prompts = (
        "何が見える", "なにが見える", "何見える", "なに見える",
        "何がみえる", "なにがみえる", "何がある", "なにがある",
    )

    jp_match = any(token in normalized for token in jp_demonstratives) and any(token in normalized for token in jp_visual_topics)
    has_scene_target = (
        any(token in normalized for token in jp_scene_targets)
        or _text_has_now_scene_token(normalized)
    )
    jp_scene_match = (
        any(token in normalized for token in jp_scene_prompts)
        or (
            has_scene_target
            and any(token in normalized for token in ("見える", "みえる", "何", "なん"))
        )
    )
    en_match = any(token in normalized_lower for token in en_demonstratives) and any(token in normalized_lower for token in en_visual_topics)
    return jp_match or jp_scene_match or en_match


def _ai_response_suggests_visual_check(ai_response: str) -> bool:
    text = (ai_response or "").strip().lower()
    if not text:
        return False

    hint_patterns = (
        "見えない", "よく見えない", "何だろう", "なんだろう", "教えてくれる",
        "わからない", "見ないと", "見せて", "見てみる", "一緒に見てみる",
        "どれどれ", "確認してみる", "確認させて", "i can't see", "can't see",
        "not sure what it is", "show me", "let me see",
    )
    return any(pattern in text for pattern in hint_patterns)


def _build_voice_response(ai_response, user_text, speaker, speaker_label, generate_voice_flag, default_label):
    """音声生成・M5Stackへpush・会話保存・レスポンス返却をまとめた共通処理"""
    memory = RobotMemory()
    label = speaker_label if speaker_label else default_label
    memory.add_conversation(speaker, user_text, ai_response, speaker_label=label)

    if ai_response:
        print(f"[AI] ({len(ai_response)}文字): {mask_names(ai_response)}")

    if generate_voice_flag and ai_response:
        voice_result = generate_voice_mixed(
            ai_response,
            speaker_id=config.VOICEVOX_SPEAKER_ID
        )
        if voice_result:
            push_to_m5stack(voice_result["source_url"])
        else:
            print("[AI] 音声生成失敗")

    return {"success": True, "response": ai_response, "error": None}


def process_chat(user_text, speaker="master", generate_voice_flag=False, speaker_label=None):
    NOISE_PATTERNS = ["ごちそう"]  # 誤認識しやすいワード
    print(f"[USER = {speaker}] {mask_names(user_text)[:80]}")
    member_data = load_member_data()
    master_label = MASTER_MASK_LABEL

    if user_text.strip() in NOISE_PATTERNS:
        ai_response = get_pc_cache_text("noise_check", "なんの音？")
        return _build_voice_response(
            ai_response, user_text, speaker, speaker_label, generate_voice_flag, master_label
        )
    # チャットを処理
    if any(kw in user_text for kw in ["イングリッシュモード", "イングイッシュモード", "英語モード", "えいごもーど"]):
        set_english_mode(True)
        ai_response = get_pc_cache_text("ok_english", "OK! Let's speak English!")
        return _build_voice_response(
            ai_response, user_text, speaker, speaker_label, generate_voice_flag, master_label
        )
    if any(kw in user_text.lower() for kw in ["japanese mode", "japanese modo", "日本語モード", "にほんごもーど"]):
        set_english_mode(False)
        ai_response = get_pc_cache_text("ok_japanese", "わかった！日本語に戻るね！")
        return _build_voice_response(
            ai_response, user_text, speaker, speaker_label, generate_voice_flag, master_label
        )

    # 明示キーワードのカメラ要求
    camera_mode = _detect_camera_mode(user_text)
    if camera_mode:
        memory = RobotMemory()
        label = speaker_label if speaker_label else master_label
        memory.add_conversation(
            speaker,
            user_text,
            f"（camera trigger: {camera_mode}）",
            speaker_label=label,
        )
        return _camera_trigger_result(
            camera_mode,
            requester="user",
            speaker=speaker,
            speaker_label=label,
        )

    # 天気は視覚ヒューリスティックより先（「今日の天気は何？」誤爆防止）
    weather_target = _detect_weather_target(user_text)
    if weather_target:
        ai_response = get_weather_response(weather_target)
        if ai_response:
            return _build_voice_response(
                ai_response, user_text, speaker, speaker_label, generate_voice_flag, master_label
            )

    if _looks_like_visual_question(user_text):
        memory = RobotMemory()
        label = speaker_label if speaker_label else master_label
        memory.add_conversation(
            speaker,
            user_text,
            "（camera trigger: transient heuristic）",
            speaker_label=label,
        )
        print("[AI-CAMERA] mode=transient source=user-heuristic")
        return _camera_trigger_result(
            "transient",
            requester="user",
            speaker=speaker,
            speaker_label=label,
        )
    # 歌唱
    if any(kw in user_text for kw in config.SONG_TRIGGER):

        def to_hira(s):
            return ''.join(
                chr(ord(c) - 0x60) if 'ァ' <= c <= 'ン' else c
                for c in s
            )
        user_hira = to_hira(user_text)

        song_id = next(
            (sid for keyword, sid in config.SONG_MAP.items() if to_hira(keyword) in user_hira),
            None
        )
        
        if song_id:
            song_url = f"{config.VOICE_SERVER_URL}/song/{song_id}"
            ai_response = "うたうよ～！"

            memory = RobotMemory()
            memory.add_conversation(
                speaker,
                user_text,
                ai_response,
                speaker_label=speaker_label if speaker_label else master_label,
            )

            push_to_m5stack(song_url)
            return {"success": True, "response": ai_response, "error": None}

    # ── 英語応答の意図判定 ──────────────
    intent = detect_intent(user_text)
    # 英語モード中は LLM も英語で返す（STT が多少ずれても英語会話を維持）
    eng_mode = is_english_mode()
    print(f"[MODE] intent={intent} english_mode={eng_mode}")
    try:
        mode = (
            "english_reply"
            if (intent == "english_reply" or eng_mode)
            else "normal"
        )
        memory      = RobotMemory()
        context     = memory.get_context(
            query=user_text,
            recent_turns=_recent_turns_for_ai_context(user_text),
        )
        ai_response = get_ai_response(user_text, context, speaker, mode=mode)
        cleaned_response, ai_camera_mode = _extract_ai_camera_mode(ai_response)

        if ai_camera_mode:
            label = speaker_label if speaker_label else master_label
            memory.add_conversation(
                speaker,
                user_text,
                f"（ai camera trigger: {ai_camera_mode}）",
                speaker_label=label,
            )
            print(f"[AI-CAMERA] mode={ai_camera_mode}")
            return _camera_trigger_result(
                ai_camera_mode,
                requester="ai",
                speaker=speaker,
                speaker_label=label,
                announce_endpoint=config.AI_VISION_TRIGGER_ENDPOINT,
            )
        else:
            ai_response = cleaned_response
            if _looks_like_visual_question(user_text) and _ai_response_suggests_visual_check(ai_response):
                label = speaker_label if speaker_label else master_label
                memory.add_conversation(
                    speaker,
                    user_text,
                    "（ai camera heuristic: transient）",
                    speaker_label=label,
                )
                print("[AI-CAMERA] mode=transient source=heuristic")
                return _camera_trigger_result(
                    "transient",
                    requester="ai",
                    speaker=speaker,
                    speaker_label=label,
                    announce_endpoint=config.AI_VISION_TRIGGER_ENDPOINT,
                )

        return _build_voice_response(
            ai_response, user_text, speaker, speaker_label, generate_voice_flag, master_label
        )

    except Exception as e:
        traceback.print_exc()
        return {"success": False, "response": None, "error": str(e)}
    

def detect_speaker(text):
    """発話内容から話者を判定（カタカナ・ひらがな両対応）

    Returns:
        "family1" / "family2" / ... / "other" / "master"
    """

    def to_hira(s):
        return ''.join(
            chr(ord(c) - 0x60) if 'ァ' <= c <= 'ン' else c
            for c in s
        )

    text_hira = to_hira(text)

    # member.jsonの家族パターンで判定
    member_data = load_member_data()
    for patterns, call_name, speaker_id in get_speaker_patterns(member_data):
        for pattern in patterns:
            if pattern and to_hira(pattern) in text_hira:
                print(f"[SPEAKER] → {speaker_id}")
                return speaker_id

    master = member_data.get("master", {})
    for master_name in get_all_names(master):
        if master_name and to_hira(master_name) in text_hira:
            print("[SPEAKER] → other")
            return "other"

    return "master"


def resolve_speaker_context(user_text: str) -> tuple[str, str]:
    speaker = detect_speaker(user_text)
    speaker_label = get_speaker_mask_label_map().get(speaker, MASTER_MASK_LABEL)
    return speaker, speaker_label
