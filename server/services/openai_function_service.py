"""StackChan向けの軽量 Function Calling 互換ルータ。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import llm_client
from config import config
from member_loader import MASTER_MASK_LABEL, unmask_names


@dataclass
class FunctionCallDecision:
    name: str
    arguments: dict


def _available_function_names(functions: list[dict]) -> set[str]:
    names: set[str] = set()
    for item in functions:
        name = item.get("name")
        if isinstance(name, str) and name:
            names.add(name)
    return names


def _extract_timer_seconds(text: str) -> int | None:
    minute_match = re.search(r"(\d+)\s*分", text)
    if minute_match:
        return int(minute_match.group(1)) * 60

    second_match = re.search(r"(\d+)\s*秒", text)
    if second_match:
        return int(second_match.group(1))

    minute_match_en = re.search(r"(\d+)\s*(minutes|minute|mins|min)\b", text, re.IGNORECASE)
    if minute_match_en:
        return int(minute_match_en.group(1)) * 60

    second_match_en = re.search(r"(\d+)\s*(seconds|second|secs|sec)\b", text, re.IGNORECASE)
    if second_match_en:
        return int(second_match_en.group(1))

    return None


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(kw in text for kw in keywords)


_MONTH_NAME_TO_NUM = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_WEEKDAY_NAME_TO_NUM = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_WEEKDAY_JA_PATTERNS = [
    (r"月曜日|げつようび|月曜", 0),
    (r"火曜日|かようび|火曜", 1),
    (r"水曜日|すいようび|水曜", 2),
    (r"木曜日|もくようび|木曜", 3),
    (r"金曜日|きんようび|金曜", 4),
    (r"土曜日|どようび|土曜", 5),
    (r"日曜日|にちようび|日曜", 6),
]


def _now() -> datetime:
    return datetime.now()


def _date_args(target: datetime) -> dict:
    return {
        "year": target.year,
        "month": target.month,
        "day": target.day,
    }


def _target_date_from_weekday(weekday_num: int) -> datetime:
    today = _now()
    delta = (weekday_num - today.weekday()) % 7
    return today + timedelta(days=delta)


def _extract_target_weekday(text: str, lower_text: str) -> int | None:
    for pattern, weekday_num in _WEEKDAY_JA_PATTERNS:
        if re.search(pattern, text):
            return weekday_num

    for weekday_name, weekday_num in _WEEKDAY_NAME_TO_NUM.items():
        if weekday_name in lower_text:
            return weekday_num

    return None


def _extract_explicit_date(text: str, lower_text: str) -> datetime | None:
    current_year = _now().year

    ja_ymd = re.search(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if ja_ymd:
        year = int(ja_ymd.group(1)) if ja_ymd.group(1) else current_year
        return datetime(year, int(ja_ymd.group(2)), int(ja_ymd.group(3)))

    iso_match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", lower_text)
    if iso_match:
        return datetime(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))

    slash_match = re.search(r"(?:(\d{4})/)?(\d{1,2})/(\d{1,2})", lower_text)
    if slash_match:
        year = int(slash_match.group(1)) if slash_match.group(1) else current_year
        return datetime(year, int(slash_match.group(2)), int(slash_match.group(3)))

    month_name_match = re.search(
        r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:,?\s*(\d{4}))?",
        lower_text,
    )
    if month_name_match:
        year = int(month_name_match.group(3)) if month_name_match.group(3) else current_year
        return datetime(year, _MONTH_NAME_TO_NUM[month_name_match.group(1)], int(month_name_match.group(2)))

    return None


def _extract_relative_date(text: str, lower_text: str) -> datetime | None:
    relative_specs = [
        (
            0,
            ["今日", "きょう"],
            ["today", "today's"],
        ),
        (
            1,
            ["明日", "あした", "あす"],
            ["tomorrow", "tomorrow's"],
        ),
        (
            -1,
            ["昨日", "きのう", "さくじつ"],
            ["yesterday", "yesterday's"],
        ),
    ]
    for offset, ja_keywords, en_keywords in relative_specs:
        if _contains_any(text, ja_keywords) or _contains_any(lower_text, en_keywords):
            return _now() + timedelta(days=offset)
    return None


def _resolve_target_date(text: str, lower_text: str, *, allow_weekday_lookup: bool) -> dict | None:
    target = _extract_relative_date(text, lower_text)
    if target is not None:
        delta = (target.date() - _now().date()).days
        return {} if delta == 0 else _date_args(target)

    explicit = _extract_explicit_date(text, lower_text)
    if explicit is not None:
        return _date_args(explicit)

    if allow_weekday_lookup:
        weekday_num = _extract_target_weekday(text, lower_text)
        if weekday_num is not None:
            return _date_args(_target_date_from_weekday(weekday_num))

    return None


def _extract_week_date_args(text: str, lower_text: str) -> dict | None:
    asks_for_week = _contains_any(
        text,
        [
            "今日何曜日",
            "きょうなんようび",
            "今日は何曜日",
            "今日の曜日",
            "明日は何曜日",
            "あしたはなんようび",
            "あすはなんようび",
            "昨日は何曜日",
            "きのうはなんようび",
            "さくじつはなんようび",
            "曜日を教えて",
            "何曜日",
        ],
    ) or _contains_any(
        lower_text,
        [
            "what day is it today",
            "what day of the week is it today",
            "today's day of the week",
            "what day is it tomorrow",
            "what day of the week is it tomorrow",
            "tomorrow's day of the week",
            "what day was yesterday",
            "what day of the week was yesterday",
            "yesterday's day of the week",
            "what day",
            "day of the week",
        ],
    )
    if not asks_for_week:
        return None

    return _resolve_target_date(text, lower_text, allow_weekday_lookup=False)


def _extract_date_args(text: str, lower_text: str) -> dict | None:
    asks_for_date = _contains_any(
        text,
        [
            "きょうはなんにち",
            "今日の日付",
            "きょうなんにち",
            "明日は何日",
            "あしたはなんにち",
            "明日の日付",
            "あしたのひづけ",
            "昨日は何日",
            "きのうはなんにち",
            "昨日の日付",
            "きのうのひづけ",
            "何日",
            "なんにち",
            "日付",
            "日にち",
        ],
    ) or _contains_any(
        lower_text,
        [
            "what's today's date",
            "what is today's date",
            "today's date",
            "what's tomorrow's date",
            "what is tomorrow's date",
            "tomorrow's date",
            "what was yesterday's date",
            "what is yesterday's date",
            "yesterday's date",
            "what date",
            "which date",
            "what day of the month",
        ],
    )
    if not asks_for_date:
        return None

    return _resolve_target_date(text, lower_text, allow_weekday_lookup=True)


def decide_function_call(user_text: str, functions: list[dict]) -> FunctionCallDecision | None:
    """ユーザー発話から Function Calling 要否を軽量判定する。"""
    text = (user_text or "").strip()
    lower_text = text.lower()
    if not text:
        return None

    names = _available_function_names(functions)
    if not names:
        return None

    # Wakeword
    if "register_wakeword" in names and (
        _contains_any(text, ["ウェイクワード登録", "起動ワード登録", "合言葉登録"])
        or _contains_any(lower_text, ["register wakeword", "register wake word", "register trigger word"])
    ):
        return FunctionCallDecision("register_wakeword", {})

    if "wakeword_enable" in names and (
        _contains_any(text, ["ウェイクワード有効", "起動ワード有効", "ウェイクワードオン"])
        or _contains_any(lower_text, ["enable wakeword", "enable wake word", "wakeword on", "wake word on"])
    ):
        return FunctionCallDecision("wakeword_enable", {})

    if "delete_wakeword" in names:
        delete_match = re.search(r"ウェイクワード\s*#?\s*(\d+)\s*.*削除|削除.*ウェイクワード\s*#?\s*(\d+)", text)
        if delete_match:
            idx = next((group for group in delete_match.groups() if group is not None), None)
            if idx is not None:
                return FunctionCallDecision("delete_wakeword", {"idx": int(idx)})

    # Timer
    if "timer_change" in names and (
        _contains_any(text, ["タイマー止め", "タイマーやめ", "タイマーキャンセル"])
        or _contains_any(lower_text, ["stop timer", "cancel timer", "turn off timer"])
    ):
        return FunctionCallDecision("timer_change", {"time": 0})

    if "timer" in names and (
        ("タイマー" in text and "セット" in text)
        or _contains_any(lower_text, ["set timer", "set a timer", "start timer"])
    ):
        seconds = _extract_timer_seconds(text)
        if seconds:
            action = "shutdown" if (
                _contains_any(text, ["電源", "シャットダウン", "オフ"])
                or _contains_any(lower_text, ["shutdown", "power off", "turn off"])
            ) else "alarm"
            return FunctionCallDecision("timer", {"time": seconds, "action": action})

    if "timer_change" in names and (
        ("タイマー" in text and _contains_any(text, ["変更", "延長", "短縮"]))
        or (
            "timer" in lower_text
            and _contains_any(lower_text, ["change", "extend", "shorten"])
        )
    ):
        seconds = _extract_timer_seconds(text)
        if seconds is not None:
            return FunctionCallDecision("timer_change", {"time": seconds})

    # Date / time
    if "get_week" in names:
        week_args = _extract_week_date_args(text, lower_text)
        if week_args is not None:
            return FunctionCallDecision("get_week", week_args)

    if "get_date" in names:
        date_args = _extract_date_args(text, lower_text)
        if date_args is not None:
            return FunctionCallDecision("get_date", date_args)

    if "get_time" in names and (
        any(
            kw in text
            for kw in [
                "今何時",
                "いまなんじ",
                "時間を教えて",
                "じかんをおしえて",
                "時間教えて",
                "じかんおしえて",
                "何時",
                "なんじ",
            ]
        )
        or _contains_any(lower_text, ["what time is it", "current time", "tell me the time"])
    ):
        return FunctionCallDecision("get_time", {})

    return None


def build_function_call_message(decision: FunctionCallDecision) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "function_call": {
            "name": decision.name,
            "arguments": json.dumps(decision.arguments, ensure_ascii=False),
        },
    }


def build_function_result_message(function_name: str, function_result: str) -> str:
    """device側 function 実行後の最終発話。

    今の StackChan の function 実行結果は、すでにユーザー向け文になっていることが多い。
    まずはそのまま返す。
    """
    result = (function_result or "").strip()
    if result:
        return result

    fallback_map = {
        "register_wakeword": "ウェイクワード登録を始めるね。",
        "wakeword_enable": "ウェイクワードを有効にしたよ。",
        "delete_wakeword": "ウェイクワードを削除したよ。",
        "timer": "タイマーを設定したよ。",
        "timer_change": "タイマーを変更したよ。",
        "get_date": "日付を確認したよ。",
        "get_time": "時刻を確認したよ。",
        "get_week": "曜日を確認したよ。",
    }
    return fallback_map.get(function_name, "できたよ。")


def render_personalized_function_result(function_name: str, function_result: str, user_text: str = "") -> str:
    """function結果を、必要なものだけペルソナ付きで言い換える。"""
    base_result = build_function_result_message(function_name, function_result)
    if function_name not in {"get_date", "get_time", "get_week"}:
        return base_result

    _master_label = MASTER_MASK_LABEL
    prompt = f"""
あなたの名前は「{config.ASSISTANT_NAME}」。{config.ASSISTANT_PERSONA}

以下の計算結果を絶対に改変せず、その事実をそのまま使って短く自然に答えてください。

ルール:
- 計算結果の日時・曜日は絶対に言い換えたり再計算しない
- 「計算結果」に含まれる事実を必ずそのまま使う
- 1〜2文、50文字以内
- 親しみやすい口調
- userの発言を繰り返しすぎない
- 話者は{_master_label}です
- 呼びかける必要がある場合は{_master_label}のような角括弧付きラベルだけを使い、「マスター」のような裸の役割名は使わない

ユーザーの質問: {user_text}
計算結果: {base_result}

    返答:"""
    try:
        rendered = (llm_client.call(prompt) or "").strip()
        return unmask_names(rendered) or base_result
    except Exception as exc:
        print(f"[FunctionPersona] render failed: {exc}")
        return base_result
