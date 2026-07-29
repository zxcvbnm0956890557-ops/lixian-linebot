from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from database import BotDatabase
from line_api import push_text


LOGGER = logging.getLogger(__name__)


def build_daily_message(database: BotDatabase) -> str:
    timezone_name = os.getenv("APP_TIMEZONE", "Asia/Taipei")
    now = datetime.now(ZoneInfo(timezone_name))
    summary = database.today_summary(now.date().isoformat(), timezone_name)
    pdf_url = os.getenv("DAILY_PDF_FOLDER_URL", "").strip()
    message = (
        f"📦 {now:%Y/%m/%d} 晚上9點出貨統計\n"
        f"訂單：{summary['orders']} 筆\n"
        f"黑貓託運單：{summary['packages']} 件\n"
        f"5斤：{summary['five']} 箱\n"
        f"10斤：{summary['ten']} 箱\n"
        f"待確認：{summary['pending']} 筆"
    )
    if pdf_url:
        message += f"\n今日PDF：{pdf_url}"
    return message


def send_daily_report(database: BotDatabase) -> None:
    target = os.getenv("LINE_REPORT_TARGET_ID", "").strip() or database.get_setting(
        "line_report_target_id"
    ).strip()
    if not target:
        LOGGER.warning("未設定 LINE_REPORT_TARGET_ID，略過晚上9點LINE統計")
        return
    if os.getenv("DRY_RUN", "1") == "1":
        LOGGER.info("DRY_RUN 每日統計：%s", build_daily_message(database))
        return
    push_text(target, build_daily_message(database))


def start_daily_scheduler(database: BotDatabase) -> BackgroundScheduler:
    timezone_name = os.getenv("APP_TIMEZONE", "Asia/Taipei")
    hour = int(os.getenv("DAILY_REPORT_HOUR", "21"))
    scheduler = BackgroundScheduler(timezone=timezone_name, daemon=True)
    scheduler.add_job(
        send_daily_report,
        trigger="cron",
        hour=hour,
        minute=0,
        args=[database],
        id="daily-line-report",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    scheduler.start()
    return scheduler
