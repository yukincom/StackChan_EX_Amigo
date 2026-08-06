# services/scheduler_service.py
"""スケジューラーサービスモジュール"""

import requests
from datetime import date
import jpholiday

from config import config
from apscheduler.schedulers.background import BackgroundScheduler
from services.weather_service import update_weather, get_weather_response
from services.voice_service import generate_voice, cleanup_old_voice_files, push_to_m5stack
from services.notification_service import process_notification
from services.discord_service import poll_discord
from member_loader import has_line_users, has_discord_users
from memory_manager import RobotMemory 


# スケジューラーインスタンス（グローバル）
scheduler = BackgroundScheduler()

# ════════════════════════════════════════
#  ユーティリティ
# ════════════════════════════════════════

def is_holiday():
    """今日が土日または日本の祝日か判定"""
    today = date.today()
    return today.weekday() >= 5 or jpholiday.is_holiday(today)


def scheduled_announcement(
    message: str,
    with_weather: bool = False,
    weekday_only: bool = True,
    weather_target: str = "today",
    holiday_only: bool = True,
):
    """定時アナウンス（Gemini不使用・スクリプト固定）

    weekday_only / holiday_only は「のみ」ではなく「対象日」フラグ:
      - 平日 ON  → 平日（土日祝以外）に実行
      - 祝日 ON  → 土日・祝日に実行
      - 両方 ON  → 毎日
      - 両方 OFF → 長期休暇扱い（スキップ）
    """
    on_weekday = bool(weekday_only)
    on_holiday = bool(holiday_only)

    # 両方オフ = 長期休暇（アナウンスしない）
    if not on_weekday and not on_holiday:
        print(f"[ANNOUNCE] 長期休暇のためスキップしました: {message[:30]}")
        return

    today_is_holiday = is_holiday()
    if today_is_holiday and not on_holiday:
        print(f"[ANNOUNCE] 祝日は対象外のためスキップ: {message[:30]}")
        return
    if (not today_is_holiday) and not on_weekday:
        print(f"[ANNOUNCE] 平日は対象外のためスキップ: {message[:30]}")
        return

    try:
        text = message
        if with_weather:
            weather = get_weather_response(weather_target)
            if weather:
                text += weather

        print(f"[ANNOUNCE] {text[:50]}")
        voice_result = generate_voice(text)
        if voice_result:
            push_to_m5stack(voice_result["source_url"])
            memory = RobotMemory()
            memory.add_conversation("schedule", text, "", speaker_label="スケジュール")

    except Exception as e:
        print(f"[ANNOUNCE] エラー: {e}")

# ════════════════════════════════════════
#  LINE / Render ポーリング
# ════════════════════════════════════════

def poll_render():
    """Renderの/pollエンドポイントをポーリング"""
    try:
        response = requests.get(f"{config.RENDER_URL}/poll", timeout=60)
        response.raise_for_status()
        data = response.json()
        if data.get("notification"):
            n = data["notification"]
            process_notification(n.get("user_id", ""), n.get("message", ""))
    except requests.exceptions.Timeout:
        pass
    except Exception as e:
        print(f"[ERROR] poll_render: {e}")


def scheduled_cleanup():
    """スケジュールドクリーンアップ実行"""
    try:
        cleanup_old_voice_files(max_age_seconds=3600, keep_latest=True)
    except Exception as e:
        print(f"[ERROR] scheduled_cleanup: {e}")


# ════════════════════════════════════════
#  スケジューラー設定
# ════════════════════════════════════════

def setup_scheduler(notification_enabled=True):
    # 再起動が多い運用でも古いwavが溜まらないよう、起動時にも1回掃除する。
    scheduled_cleanup()

    if notification_enabled:
        if has_line_users():
            scheduler.add_job(poll_render, "interval", seconds=config.POLL_INTERVAL)
            print("[SCHEDULER] LINE ポーリング開始")

        if has_discord_users():
            scheduler.add_job(poll_discord, "interval", seconds=config.POLL_INTERVAL)
            print("[SCHEDULER] Discord ポーリング開始")

    scheduler.add_job(scheduled_cleanup, "interval", hours=1)

    # 天気更新
    scheduler.add_job(update_weather, "cron",
        hour=config.WEATHER_MORNING_HOUR, minute=config.WEATHER_MORNING_MINUTE, args=["morning"])
    scheduler.add_job(update_weather, "cron",
        hour=config.WEATHER_FORECAST_HOUR, minute=config.WEATHER_FORECAST_MINUTE, args=["forecast"])
    scheduler.add_job(update_weather, "cron",
        hour=config.WEATHER_CONFIRMED_HOUR, minute=config.WEATHER_CONFIRMED_MINUTE, args=["confirmed"])

    # 定時アナウンス（リストをループで登録）
    for item in config.ANNOUNCEMENTS:
        if "weekday_only" not in item and "holiday_only" not in item:
            weekday_on, holiday_on = True, True
        else:
            weekday_on = bool(item.get("weekday_only", False))
            holiday_on = bool(item.get("holiday_only", False))
            # 旧仕様「両方 false = 毎日」。明示 long vacation 以外は毎日へ
            if (
                not weekday_on
                and not holiday_on
                and not item.get("_explicit_long_vacation")
            ):
                weekday_on, holiday_on = True, True

        scheduler.add_job(
            scheduled_announcement, "cron",
            hour=item["hour"], minute=item["minute"],
            kwargs={
                "message": item["message"],
                "with_weather": item.get("with_weather", False),
                "weekday_only": weekday_on,
                "weather_target": item.get("weather_target", "today"),
                "holiday_only": holiday_on,
            },
        )
    print(f"[SCHEDULER] 定時アナウンス {len(config.ANNOUNCEMENTS)} 件登録")

    scheduler.start()


def start_scheduler():
    if not scheduler.running:
        scheduler.start()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
