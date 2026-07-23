from __future__ import annotations

import logging
import os
import re
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
TEST_REPLY_PREFIX = "#測試"


def split_test_reply_prefix(raw_text: str) -> tuple[bool, str]:
    """只有明確加上 #測試 的訂單，才允許在 DRY_RUN 模式回覆 LINE。"""
    if not raw_text.lstrip().startswith(TEST_REPLY_PREFIX):
        return False, raw_text
    cleaned = re.sub(r"^\s*#測試\s*", "", raw_text, count=1)
    return True, cleaned.strip()


def format_ready_batch(rows: list[tuple[object, int, str]]) -> str:
    lines = [f"✅ 已辨識 {len(rows)} 筆訂單，資料檢查完成"]
    for index, (order, package_count, sheet_status) in enumerate(rows, start=1):
        lines.extend(
            [
                "",
                f"【第 {index} 筆】{order.recipient_name}／{order.recipient_phone}",
                order.recipient_address,
                f"規格：5斤 {order.five_jin_boxes} 箱／10斤 {order.ten_jin_boxes} 箱",
                f"黑貓件數：{package_count} 件",
                f"寄件人姓名：{order.sender_name}",
                f"備註：{order.note or '無'}",
                f"狀態：{sheet_status}",
            ]
        )
    return "\n".join(lines)


def format_pending_batch(extraction, issues: list[str]) -> str:
    known = []
    if extraction:
        for index, order in enumerate(extraction.orders, start=1):
            known.extend(
                [
                    f"第 {index} 筆：{order.recipient_name or '姓名未確認'}／"
                    f"{order.recipient_phone or '電話未確認'}",
                    f"地址：{order.recipient_address or '未確認'}",
                    f"規格：5斤{order.five_jin_boxes}／10斤{order.ten_jin_boxes}",
                ]
            )
    issue_text = "\n".join(f"• {issue}" for issue in issues)
    return (
        "⚠️ 這一批訂單有資料無法確定，整批尚未寫入\n"
        + "\n".join(known)
        + f"\n需要確認：\n{issue_text}"
    )


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
        test_reply = False
        try:
            test_reply, parse_text = split_test_reply_prefix(conversation["raw_text"])
            LOGGER.info("訂單 %s 開始 AI 解析", conversation["id"])
            extraction = self.parser.parse(parse_text)
            LOGGER.info(
                "訂單 %s AI 解析完成，共 %s 筆，開始驗證",
                conversation["id"],
                len(extraction.orders),
            )
            issues = list(extraction.batch_ambiguities)
            if not extraction.orders:
                issues.append("沒有辨識到任何完整訂單")

            ready_orders = []
            for index, item in enumerate(extraction.orders, start=1):
                validation = validate_extraction(item)
                if validation.order is None:
                    issues.extend(f"第 {index} 筆：{issue}" for issue in validation.issues)
                else:
                    ready_orders.append(validation.order)

            if issues:
                issues = list(dict.fromkeys(issues))
                self.database.mark_pending(conversation["id"], extraction, issues)
                LOGGER.info(
                    "訂單 %s 整批需要人工確認（%s 筆，%s 項）",
                    conversation["id"],
                    len(extraction.orders),
                    len(issues),
                )
                if not self.dry_run or test_reply:
                    self.notifier(
                        conversation["destination_id"],
                        format_pending_batch(extraction, issues),
                    )
                return

            rows = []
            for order in ready_orders:
                packages = split_packages(order.five_jin_boxes, order.ten_jin_boxes)
                sheet_status = (
                    "測試模式（未寫入Google試算表）"
                    if self.dry_run
                    else self.sheets.append_order(
                        order, len(packages), conversation["raw_text"]
                    )
                )
                rows.append((order, len(packages), sheet_status))

            self.database.save_ready_orders(conversation, extraction, rows)
            LOGGER.info(
                "訂單 %s 已完成，共 %s 筆、黑貓件數 %s",
                conversation["id"],
                len(rows),
                sum(row[1] for row in rows),
            )
            if not self.dry_run or test_reply:
                self.notifier(
                    conversation["destination_id"],
                    format_ready_batch(rows),
                )
        except Exception as error:
            LOGGER.exception("訂單 %s 處理失敗", conversation["id"])
            self.database.mark_pending(
                conversation["id"], extraction, [f"系統處理失敗：{type(error).__name__}"]
            )
            if not self.dry_run or test_reply:
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
