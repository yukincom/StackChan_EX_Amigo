# services/weather_service.py
"""
天気取得サービス（Open-Meteo API使用）
APIキー不要・無料・pip install requests だけでOK

対象地域: .envの WEATHER_LATITUDE / WEATHER_LONGITUDE で設定
"""

import json
import re
import requests
from datetime import datetime, timedelta
from pathlib import Path
from config import config

def _parse_coordinate(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


# 座標（.envから取得）
LATITUDE = _parse_coordinate(config.WEATHER_LATITUDE)
LONGITUDE = _parse_coordinate(config.WEATHER_LONGITUDE)

# 天気コード → 日本語変換
WEATHER_CODE = {
    0:  "快晴",
    1:  "晴れ",
    2:  "晴れ時々くもり",
    3:  "くもり",
    45: "霧",
    48: "霧",
    51: "小雨",
    53: "雨",
    55: "強い雨",
    61: "小雨",
    63: "雨",
    65: "強い雨",
    71: "小雪",
    73: "雪",
    75: "大雪",
    77: "みぞれ",
    80: "にわか雨",
    81: "にわか雨",
    82: "強いにわか雨",
    85: "にわか雪",
    86: "強いにわか雪",
    95: "雷雨",
    96: "雷雨",
    99: "激しい雷雨",
}

RAIN_CODES = {51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
WEATHER_CACHE_FILE = "weather_cache.json"
WEATHER_REQUEST_TIMEOUT = 10
WEATHER_REQUEST_RETRIES = 1
WEATHER_CONFIRMED_TIMEOUT = 15
WEATHER_CONFIRMED_RETRIES = 2


def _weather_enabled() -> bool:
    return LATITUDE is not None and LONGITUDE is not None


def _ensure_weather_enabled():
    if not _weather_enabled():
        raise RuntimeError("WEATHER_LATITUDE / WEATHER_LONGITUDE が未設定です")


def _request_weather(params, timeout=WEATHER_REQUEST_TIMEOUT, retries=WEATHER_REQUEST_RETRIES):
    url = "https://api.open-meteo.com/v1/forecast"
    last_error = None

    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_error = exc
            if attempt >= retries:
                raise
        except Exception:
            raise

    raise last_error


def _merge_weather_data(daily_data, hourly_data=None):
    merged = {
        "daily": daily_data.get("daily", {}),
        "daily_units": daily_data.get("daily_units", {}),
    }
    if hourly_data:
        merged["hourly"] = hourly_data.get("hourly", {})
        merged["hourly_units"] = hourly_data.get("hourly_units", {})
    return merged


def _fetch_forecast_daily_weather():
    params = {
        "latitude":  LATITUDE,
        "longitude": LONGITUDE,
        "daily": [
            "weathercode",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_probability_max",
        ],
        "timezone":      "Asia/Tokyo",
        "forecast_days": 2,
    }
    return _request_weather(params)


def _fetch_forecast_hourly_weather():
    params = {
        "latitude":  LATITUDE,
        "longitude": LONGITUDE,
        "hourly": [
            "weathercode",
            "precipitation_probability",
            "rain",
            "showers",
        ],
        "timezone":      "Asia/Tokyo",
        "forecast_days": 2,
    }
    return _request_weather(params)


def _fetch_forecast_weather(hourly_optional=False):
    """Open-Meteo APIから今日と明日の予報を取得"""
    daily_data = _fetch_forecast_daily_weather()

    try:
        hourly_data = _fetch_forecast_hourly_weather()
    except Exception as exc:
        if not hourly_optional:
            raise
        print(f"[WEATHER] hourly 詳細の取得失敗: {exc}")
        return _merge_weather_data(daily_data)

    return _merge_weather_data(daily_data, hourly_data)


def _fetch_confirmed_weather():
    """Open-Meteo APIから現在の観測値を取得"""
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "current": ["temperature_2m", "weather_code"],
        "timezone": "Asia/Tokyo",
    }
    return _request_weather(
        params,
        timeout=WEATHER_CONFIRMED_TIMEOUT,
        retries=WEATHER_CONFIRMED_RETRIES,
    )


def _fetch_confirmed_hourly_weather():
    """current が失敗したときの代替。今日の hourly から最新時刻を拾う。"""
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": ["temperature_2m", "weathercode"],
        "timezone": "Asia/Tokyo",
        "forecast_days": 1,
    }
    return _request_weather(
        params,
        timeout=WEATHER_CONFIRMED_TIMEOUT,
        retries=WEATHER_CONFIRMED_RETRIES,
    )


def _weather_cache_path():
    return Path(config.MEMORY_DIR) / WEATHER_CACHE_FILE


def _load_weather_cache():
    path = _weather_cache_path()
    if not path.exists():
        return {"today_forecast": None, "forecast": None, "confirmed_history": {}}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"today_forecast": None, "forecast": None, "confirmed_history": {}}

    return {
        "today_forecast": data.get("today_forecast"),
        "forecast": data.get("forecast"),
        "confirmed_history": data.get("confirmed_history", {}),
    }


def _save_weather_cache(cache):
    path = _weather_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _get_series(data, *keys):
    for key in keys:
        if key in data:
            return data[key]
    return []


def _get_value(data, idx, *keys):
    series = _get_series(data, *keys)
    return series[idx] if idx < len(series) else None


def _format_temp(value):
    return f"{value:.0f}℃" if _is_number(value) else None


def _format_rain_prob(value):
    return f"{value:.0f}%" if _is_number(value) else None


def _format_hour(dt):
    return f"{dt.hour}時ごろ"


def _format_day_line(daily, idx, label):
    code = _get_value(daily, idx, "weathercode", "weather_code")
    t_max = _get_value(daily, idx, "temperature_2m_max")
    t_min = _get_value(daily, idx, "temperature_2m_min")
    rain = _get_value(daily, idx, "precipitation_probability_max")

    if code is None:
        parts = [f"- {label}: 詳しいお天気は不明"]
    else:
        parts = [f"- {label}: {WEATHER_CODE.get(code, f'不明({code})')}"]

    temp_parts = []
    if _is_number(t_max):
        temp_parts.append(f"最高{t_max:.0f}℃")
    if _is_number(t_min):
        temp_parts.append(f"最低{t_min:.0f}℃")
    if temp_parts:
        parts.append(" / ".join(temp_parts))

    rain_text = _format_rain_prob(rain)
    if rain_text:
        parts.append(f"降水確率{rain_text}")

    return "、".join(parts)


def _get_target_date(daily, idx):
    return _get_value(daily, idx, "time")


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _tomorrow_str():
    return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")


def _get_current_payload(data):
    return data.get("current_weather") or data.get("current") or {}


def _get_current_value(data, *keys):
    payload = _get_current_payload(data)
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _now_local():
    return datetime.now()


def _hour_is_rainy(point):
    if _is_number(point["rain"]) and point["rain"] > 0:
        return True
    if _is_number(point["showers"]) and point["showers"] > 0:
        return True
    if point["code"] in RAIN_CODES:
        return True
    if _is_number(point["prob"]) and point["prob"] >= 50:
        return True
    return False


def _get_hourly_points(data, date_str, remaining_only=False):
    if not date_str:
        return []

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    points = []
    now = _now_local()

    for idx, ts in enumerate(times):
        if not ts.startswith(date_str):
            continue

        point_time = datetime.fromisoformat(ts)
        point_end = point_time + timedelta(hours=1)
        if remaining_only and point_end <= now:
            continue

        point = {
            "time": point_time,
            "code": _get_value(hourly, idx, "weathercode", "weather_code"),
            "prob": _get_value(hourly, idx, "precipitation_probability"),
            "rain": _get_value(hourly, idx, "rain"),
            "showers": _get_value(hourly, idx, "showers"),
        }
        point["is_rainy"] = _hour_is_rainy(point)
        points.append(point)

    return points


def _get_rain_intervals(points):
    intervals = []
    start = None
    end = None

    for point in points:
        if point["is_rainy"]:
            if start is None:
                start = point["time"]
            end = point["time"] + timedelta(hours=1)
        elif start is not None:
            intervals.append((start, end))
            start = None
            end = None

    if start is not None:
        intervals.append((start, end))

    return intervals


def _build_rain_timing_comment(points):
    if not points:
        return ""

    intervals = _get_rain_intervals(points)
    if not intervals:
        return ""

    start = intervals[0][0]
    end = intervals[-1][1]
    starts_early = start.hour <= 3
    ends_next_day = end.date() > start.date()

    if len(intervals) >= 3:
        if starts_early:
            if ends_next_day:
                return "朝から雨で、断続的に明日まで降り続くみたい！詳しい時刻は、お天気サイトで調べてね！"
            return "朝から雨で、断続的に降り続くみたい！詳しい時刻は、お天気サイトで調べてね！"
        if ends_next_day:
            return f"{_format_hour(start)}から降り始めて、断続的に明日まで降り続くみたい！詳しい時刻は、お天気サイトで調べてね！"
        return f"{_format_hour(start)}から降り始めて、断続的に降り続くみたい！詳しい時刻は、お天気サイトで調べてね！"

    if len(intervals) == 2:
        if starts_early:
            if ends_next_day:
                return "朝から雨で、途中やむ時間はありそうだけど、明日まで降り続くみたい！"
            return f"朝から雨で、途中やむ時間はありそうだけど、{_format_hour(end)}に降り終わりそうだよ。"
        if ends_next_day:
            return f"{_format_hour(start)}から降り始めて、途中やむ時間はありそうだけど、明日まで降り続くみたい！"
        return f"{_format_hour(start)}から降り始めて、途中やむ時間はありそうだけど、{_format_hour(end)}に降り終わりそうだよ。"

    if starts_early:
        if ends_next_day:
            return "朝から雨で、明日まで降り続くみたい！"
        return f"朝から雨で、{_format_hour(end)}に降り終わりそうだよ。"

    if ends_next_day:
        return f"{_format_hour(start)}から降り始めて、明日まで降り続くみたい！"

    return f"{_format_hour(start)}から降り始めて、{_format_hour(end)}に降り終わりそうだよ。"


def _day_has_rain(data, idx, remaining_only=False):
    daily = data.get("daily", {})
    date_str = _get_target_date(daily, idx)
    points = _get_hourly_points(data, date_str, remaining_only=remaining_only)

    if any(point["is_rainy"] for point in points):
        return True
    if remaining_only:
        return False

    code = _get_value(daily, idx, "weathercode", "weather_code")
    rain = _get_value(daily, idx, "precipitation_probability_max")
    if code in RAIN_CODES:
        return True
    if _is_number(rain) and rain >= 50:
        return True
    return False


def _build_rain_comment(data, idx, label, remaining_only=False):
    daily = data.get("daily", {})
    date_str = _get_target_date(daily, idx)
    points = _get_hourly_points(data, date_str, remaining_only=remaining_only)

    timing_comment = _build_rain_timing_comment(points)

    parts = []
    if timing_comment:
        parts.append(f"{label}は雨が降りそうだよ！{timing_comment}")
    else:
        rain = _get_value(daily, idx, "precipitation_probability_max")
        if _is_number(rain) and rain >= 70:
            parts.append(f"{label}は雨だよ！")
        else:
            parts.append(f"{label}は雨が降るかもしれないよ。")

    parts.append("傘を持って行ってね！")
    return "".join(parts)


def _build_remaining_weather_comment(points, label):
    if not points:
        return f"{label}のこのあとの詳しいお天気はよくわからなかった。"

    code = points[0]["code"]
    if code in (0, 1):
        return f"{label}はこのあと晴れそうだよ！"
    if code in (2, 3, 45, 48):
        return f"{label}はこのあとくもりみたい。"
    if code in SNOW_CODES:
        return f"{label}はこのあと雪が降るみたい！"
    return f"{label}はこのあと{WEATHER_CODE.get(code, 'いろいろなお天気')}みたい。"


def _build_temperature_comment(t_max):
    if not _is_number(t_max):
        return ""

    if t_max <= 10:
        return f"最高気温は{t_max:.0f}度、かなり寒いよ！暖かくしてね！"
    if t_max <= 18:
        return f"最高気温は{t_max:.0f}度、寒そうだよ！"
    if t_max <= 26:
        return f"最高気温は{t_max:.0f}度、過ごしやすい気温だね！"
    if t_max <= 29:
        return f"最高気温は{t_max:.0f}度、暑いみたい！"
    return f"最高気温は{t_max:.0f}度、かなり暑いみたい！出かけるときは気をつけてね！"


def _build_diff_comment(t_max, t_min):
    if not (_is_number(t_max) and _is_number(t_min)):
        return ""
    if t_max - t_min >= 7:
        return f"寒暖差が{t_max - t_min:.0f}度もあるから、体調に気をつけてね！"
    return ""


def _build_daily_weather_response(data, idx, target):
    daily = data["daily"]
    label = "今日" if target == "today" else "明日"
    remaining_only = target == "today"
    remaining_points = _get_hourly_points(data, _get_target_date(daily, idx), remaining_only=remaining_only)
    remaining_only = remaining_only and bool(remaining_points)

    code = _get_value(daily, idx, "weathercode", "weather_code")
    t_max = _get_value(daily, idx, "temperature_2m_max")
    t_min = _get_value(daily, idx, "temperature_2m_min")

    if code is None:
        weather_comment = f"{label}の詳しいお天気はよくわからなかった。"
        if _day_has_rain(data, idx, remaining_only=remaining_only):
            weather_comment += f"でも、{_build_rain_comment(data, idx, label, remaining_only=remaining_only)}"
        elif remaining_only:
            weather_comment = _build_remaining_weather_comment(remaining_points, label)
    elif _day_has_rain(data, idx, remaining_only=remaining_only):
        weather_comment = _build_rain_comment(data, idx, label, remaining_only=remaining_only)
    elif remaining_only:
        weather_comment = _build_remaining_weather_comment(remaining_points, label)
    elif code in (0, 1):
        weather_comment = f"{label}は晴れるみたいだよ！"
    elif code in (2, 3, 45, 48):
        weather_comment = f"{label}はくもりみたい。"
    elif code in SNOW_CODES:
        weather_comment = f"{label}は雪が降るんだって！どのくらい降るのかなー！"
    else:
        weather_comment = f"{label}は{WEATHER_CODE.get(code, 'いろいろなお天気')}みたい。"

    temp_comment = _build_temperature_comment(t_max)
    diff_comment = _build_diff_comment(t_max, t_min)
    return f"{weather_comment}{temp_comment}{diff_comment}"


def _build_forecast_record(data):
    daily = data["daily"]
    target_date = _get_target_date(daily, 1) or _tomorrow_str()
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    code = _get_value(daily, 1, "weathercode", "weather_code")
    return {
        "date": target_date,
        "fetched_at": fetched_at,
        "section_line": _format_day_line(daily, 1, "明日の予報"),
        "response_text": _build_daily_weather_response(data, 1, "tomorrow"),
        "weather_text": WEATHER_CODE.get(code, f"不明({code})") if code is not None else None,
    }


def _build_today_forecast_record(data):
    daily = data["daily"]
    target_date = _get_target_date(daily, 0) or _today_str()
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    code = _get_value(daily, 0, "weathercode", "weather_code")
    return {
        "date": target_date,
        "fetched_at": fetched_at,
        "section_line": _format_day_line(daily, 0, "今日の予報"),
        "response_text": _build_daily_weather_response(data, 0, "today"),
        "weather_text": WEATHER_CODE.get(code, f"不明({code})") if code is not None else None,
    }


def _build_confirmed_record_from_weather_text(weather_text, temp=None, source="current"):
    if source == "current":
        section_label = "今日の確定"
        summary_weather = weather_text
    elif source == "hourly":
        section_label = "今日の確定代替"
        summary_weather = f"{weather_text}（時間帯推定）"
    else:
        section_label = "今日の予報代替"
        summary_weather = f"{weather_text}（予報代替）"

    section_line = f"- {section_label}: {weather_text}"
    summary_line = f"天気：{summary_weather}"

    if _is_number(temp):
        section_line += f"、気温{temp:.0f}℃"
        summary_line += f"　気温：{temp:.0f}度"

    return {
        "date": _today_str(),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "section_line": section_line,
        "summary_line": summary_line,
    }


def _build_confirmed_record(code, temp, source="current"):
    if code is None:
        weather_text = "詳しいお天気は不明"
    else:
        weather_text = WEATHER_CODE.get(code, f"不明({code})")
    return _build_confirmed_record_from_weather_text(weather_text, temp=temp, source=source)


def _build_confirmed_hourly_record(data):
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        raise RuntimeError("confirmed hourly fallback has no time data")

    now = _now_local()
    best_idx = None
    best_time = None

    for idx, ts in enumerate(times):
        point_time = datetime.fromisoformat(ts)
        if point_time > now:
            continue
        if best_time is None or point_time > best_time:
            best_idx = idx
            best_time = point_time

    if best_idx is None:
        best_idx = 0

    code = _get_value(hourly, best_idx, "weathercode", "weather_code")
    temp = _get_value(hourly, best_idx, "temperature_2m")
    return _build_confirmed_record(code, temp, source="hourly")


def _build_confirmed_record_from_forecast_cache(cache):
    forecast = cache.get("today_forecast")
    if not forecast or forecast.get("date") != _today_str():
        return None

    weather_text = forecast.get("weather_text") or "詳しいお天気は不明"
    return _build_confirmed_record_from_weather_text(weather_text, source="forecast")


def _get_cached_forecast_response():
    cache = _load_weather_cache()
    forecast = cache.get("forecast")
    if not forecast:
        return None
    if forecast.get("date") != _tomorrow_str():
        return None
    return forecast.get("response_text")


def _get_cached_today_forecast_response():
    cache = _load_weather_cache()
    forecast = cache.get("today_forecast")
    if not forecast:
        return None
    if forecast.get("date") != _today_str():
        return None
    return forecast.get("response_text")


def _build_weather_section(cache):
    lines = ["## 天気"]

    today_forecast = cache.get("today_forecast")
    if today_forecast and today_forecast.get("date") == _today_str():
        lines.append(today_forecast["section_line"])
        if today_forecast.get("fetched_at"):
            lines.append(f"- 今日予報更新: {today_forecast['fetched_at']}")

    forecast = cache.get("forecast")
    if forecast and forecast.get("date") == _tomorrow_str():
        lines.append(forecast["section_line"])
        if forecast.get("fetched_at"):
            lines.append(f"- 明日予報更新: {forecast['fetched_at']}")

    confirmed = cache.get("confirmed_history", {}).get(_today_str())
    if confirmed:
        lines.append(confirmed["section_line"])
        if confirmed.get("fetched_at"):
            lines.append(f"- 今日確定更新: {confirmed['fetched_at']}")

    if len(lines) == 1:
        lines.append("- まだ天気情報はありません")

    return "\n".join(lines) + "\n"


def _update_today_md(weather_section):
    """today.md の ## 天気 セクションを上書き（memory_manager ロック経由）"""
    from memory_manager import update_today

    def mutator(content: str) -> str:
        if not content:
            print("[WEATHER] today.md を新規作成して天気を記録")
            return weather_section + "\n"

        pattern = r"## 天気\n.*?(?=\n## |\Z)"
        if re.search(pattern, content, re.DOTALL):
            return re.sub(pattern, weather_section.rstrip(), content, flags=re.DOTALL)
        # セクションがなければ先頭に追加
        return weather_section + "\n" + content

    update_today(mutator)
    print("[WEATHER] today.md を更新しました")


def update_weather(mode):
    """天気更新の共通処理"""
    if not _weather_enabled():
        print("[WEATHER] 座標未設定のためスキップ")
        return
    try:
        cache = _load_weather_cache()

        if mode == "morning":
            data = _fetch_forecast_weather(hourly_optional=True)
            cache["today_forecast"] = _build_today_forecast_record(data)
            label = "今日の予報"
        elif mode == "forecast":
            data = _fetch_forecast_weather(hourly_optional=True)
            cache["forecast"] = _build_forecast_record(data)
            label = "明日の予報"
        elif mode == "confirmed":
            history = cache.setdefault("confirmed_history", {})
            try:
                data = _fetch_confirmed_weather()
                record = _build_confirmed_record(
                    _get_current_value(data, "weathercode", "weather_code"),
                    _get_current_value(data, "temperature", "temperature_2m"),
                    source="current",
                )
            except Exception as current_exc:
                print(f"[WEATHER] current 詳細の取得失敗: {current_exc}")
                try:
                    hourly_data = _fetch_confirmed_hourly_weather()
                    record = _build_confirmed_hourly_record(hourly_data)
                except Exception as hourly_exc:
                    print(f"[WEATHER] confirmed hourly 代替の取得失敗: {hourly_exc}")
                    record = _build_confirmed_record_from_forecast_cache(cache)
                    if record is None:
                        raise current_exc
            history[record["date"]] = record
            label = "当日の確定天気"
        else:
            raise ValueError(f"unknown weather mode: {mode}")

        _save_weather_cache(cache)
        section = _build_weather_section(cache)
        _update_today_md(section)
        print(f"[WEATHER] {label}を記録:\n{section}")
    except Exception as e:
        labels = {
            "morning": "今日の予報",
            "forecast": "明日の予報",
            "confirmed": "当日の確定天気",
        }
        label = labels.get(mode, "天気更新")
        print(f"[WEATHER] {label}の更新失敗: {e}")


def get_weather_response(target="today"):
    """
    「今日/明日の天気は？」に対する返答テキストを生成
    → chat_service.py から呼ぶ

    target: "today" or "tomorrow"
    """
    if not _weather_enabled():
        return None

    if target == "tomorrow":
        cached = _get_cached_forecast_response()
        if cached:
            return cached

    try:
        _ensure_weather_enabled()
        data = _fetch_forecast_weather(hourly_optional=True)
        idx = 0 if target == "today" else 1
        return _build_daily_weather_response(data, idx, target)

    except Exception as e:
        print(f"[WEATHER] 天気返答生成失敗: {e}")
        if target == "today":
            return _get_cached_today_forecast_response()
        return None


def get_confirmed_weather_summary(date_str):
    cache = _load_weather_cache()
    record = cache.get("confirmed_history", {}).get(date_str)
    if record:
        return record.get("summary_line")

    if date_str == _today_str():
        fallback = _build_confirmed_record_from_forecast_cache(cache)
        if fallback:
            return fallback.get("summary_line")

    return None
