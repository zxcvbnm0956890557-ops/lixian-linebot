from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from daily_report import start_daily_scheduler
from database import BotDatabase
from line_api import line_access_token_fingerprint, verify_line_signature
from worker import OrderWorker


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger(__name__)

app = Flask(__name__)
database = BotDatabase(os.getenv("DATABASE_PATH", str(ROOT / "data" / "bot.db")))
_services_lock = threading.Lock()
_services_started = False


def start_background_services() -> None:
    global _services_started
    with _services_lock:
        if _services_started:
            return
        LOGGER.info("LINE 權杖指紋：%s", line_access_token_fingerprint())
        OrderWorker(database).start()
        start_daily_scheduler(database)
        _services_started = True


def _event_record(event: dict) -> dict | None:
    if event.get("type") != "message" or event.get("message", {}).get("type") != "text":
        return None
    source = event.get("source", {})
    user_id = source.get("userId") or "unknown-user"
    destination_id = source.get("groupId") or source.get("roomId") or user_id
    message = event["message"]
    event_id = event.get("webhookEventId") or message.get("id")
    if not event_id:
        return None
    return {
        "event_id": event_id,
        "conversation_key": f"{destination_id}:{user_id}",
        "destination_id": destination_id,
        "user_id": user_id,
        "message_id": message.get("id", event_id),
        "body": message.get("text", "").strip(),
        "timestamp_ms": int(event.get("timestamp", 0)),
    }


@app.post("/callback")
def callback():
    raw_body = request.get_data(cache=False)
    signature = request.headers.get("X-Line-Signature", "")
    if not verify_line_signature(raw_body, signature):
        return "Invalid signature", 400
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return "Invalid JSON", 400

    accepted = 0
    for event in payload.get("events", []):
        record = _event_record(event)
        if record and record["body"] and database.enqueue_event(record):
            accepted += 1

    # 只做快速落盤就回 200；AI、Google Sheet、LINE 回覆由背景工作處理。
    return jsonify({"accepted": accepted}), 200


@app.get("/health")
def health():
    return jsonify({"ok": True}), 200


@app.get("/status")
def status():
    credentials_configured = bool(
        os.getenv("GOOGLE_CREDENTIALS_JSON") or os.getenv("GOOGLE_CREDENTIALS")
    )
    return jsonify(
        {
            "ok": True,
            "version": os.getenv("RENDER_GIT_COMMIT", "local")[:7],
            "dry_run": os.getenv("DRY_RUN", "1") == "1",
            "google_test_sheet_configured": bool(
                os.getenv("GOOGLE_SHEET_ID") and credentials_configured
            ),
            "persistent_database": str(database.path).startswith("/var/data/"),
            "daily_report_target_configured": bool(
                os.getenv("LINE_REPORT_TARGET_ID", "").strip()
                or database.get_setting("line_report_target_id").strip()
            ),
            "conversations": database.status_counts(),
        }
    ), 200


start_background_services()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
