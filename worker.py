from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

from blackcat import split_packages
from database import BotDatabase
from line_api import push_text
from order_parser import OrderParser
from sheets import SheetsWriter
from validation import validate_extraction


LOGGER = logging.getLogger(__name__)


def format_ready_message(order, package_count: int, sheet_status: str) -> str:
    sender_line = order.sender_name
    return (
        "✅ 訂單資料檢查完成\n"
        f"收件人：{order.recipient_name}\n"
        f"手機：{order.recipient_phone}\n"
        f"地址：{order.recipient_address}\n"
        f"規格：5斤 {order.five_jin_boxes} 箱／10斤 {order.ten_jin_boxes} 箱\n"
        f"黑貓件數：{package_count} 件\n"
        f"寄件人姓名：{sender_line}\n"
        f"備註：{order.note or '無'}\n"
        f"狀態：{sheet_status}"
    )


def format_pending_message(extraction, issues: list[str]) -> str:
    known = []
    if extraction:
        known.extend(
            [
                f"辨識姓名：{extraction.recipient_name or '未確認'}",
                f"辨識電話：{extraction.recipient_phone or '未確認'}",
                f"辨識地址：{extraction.recipient_address or '未確認'}",
                f"辨識規格：5斤{extraction.five_jin_boxes}／10斤{extraction.ten_jin_boxes}",
            ]
        )
    issue_text = "\n".join(f"• {issue}" for issue in issues)
    return "⚠️ 訂單尚未寫入黑貓資料\n" + "\n".join(known) + f"\n需要確認：\n{issue_text}"


class OrderWorker:
    def __init__(
        self,
        database: BotDatabase,
        parser: OrderParser | None = None,
        sheets: SheetsWriter | None = None,
        notifier: Callable[[str, str], None] = push_text,
    ):
        self.database = database
        self.parser = parser or OrderParser()
        self.sheets = sheets or SheetsWriter()
        self.notifier = notifier
        self.quiet_seconds = int(os.getenv("ORDER_QUIET_SECONDS", "30"))
        self.dry_run = os.getenv("DRY_RUN", "1") == "1"
        self._stop = threading.Event()

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, name="order-worker", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.wait(1):
            try:
                self.run_once()
            except Exception:
                LOGGER.exception("背景訂單處理發生錯誤")

    def run_once(self) -> None:
        self.database.ingest_queued_events()
        for conversation in self.database.quiet_conversations(self.quiet_seconds):
            if not self.database.mark_processing(conversation["id"]):
                continue
            self._process(conversation)

    def _process(self, conversation) -> None:
        extraction = None
        try:
            LOGGER.info("訂單 %s 開始 AI 解析", conversation["id"])
            extraction = self.parser.parse(conversation["raw_text"])
            LOGGER.info("訂單 %s AI 解析完成，開始驗證", conversation["id"])
            validation = validate_extraction(extraction)
            if validation.order is None:
                issues = list(validation.issues)
                self.database.mark_pending(conversation["id"], extraction, issues)
                LOGGER.info("訂單 %s 需要人工確認（%s 項）", conversation["id"], len(issues))
                if not self.dry_run:
                    self.notifier(conversation["destination_id"], format_pending_message(extraction, issues))
                return

            order = validation.order
            packages = split_packages(order.five_jin_boxes, order.ten_jin_boxes)
            sheet_status = self.sheets.append_order(order, len(packages), conversation["raw_text"])
            self.database.save_ready_order(
                conversation, extraction, order, len(packages), sheet_status
            )
            LOGGER.info("訂單 %s 已完成，黑貓件數 %s", conversation["id"], len(packages))
            if not self.dry_run:
                self.notifier(
                    conversation["destination_id"],
                    format_ready_message(order, len(packages), sheet_status),
                )
        except Exception as error:
            LOGGER.exception("訂單 %s 處理失敗", conversation["id"])
            self.database.mark_pending(
                conversation["id"], extraction, [f"系統處理失敗：{type(error).__name__}"]
            )
            if not self.dry_run:
                try:
                    self.notifier(
                        conversation["destination_id"],
                        "⚠️ 這筆訂單尚未寫入，系統已保留原始訊息供檢查。",
                    )
                except Exception:
                    LOGGER.exception("LINE 錯誤通知失敗")


def wait_until_idle(database: BotDatabase, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        counts = database.status_counts()
        if counts.get("collecting", 0) == 0 and counts.get("processing", 0) == 0:
            return True
        time.sleep(0.1)
    return False
