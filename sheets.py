from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

from models import CleanOrder


HEADERS = [
    "時間戳記", "來源", "訂購人姓名", "收件人姓名", "收件人電話", "收件地址",
    "5斤箱數", "10斤箱數", "寄件人姓名", "寄件人電話", "寄件人地址", "備註",
    "收據資訊", "AI信心", "黑貓件數", "狀態", "原始LINE文字",
]


class SheetsWriter:
    def __init__(self):
        self.sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
        self.worksheet_name = os.getenv("GOOGLE_WORKSHEET_NAME", "LINE訂單_Codex測試")
        self.credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "") or os.getenv(
            "GOOGLE_CREDENTIALS", ""
        )

    @property
    def configured(self) -> bool:
        return bool(self.sheet_id and self.credentials_json)

    def _worksheet(self):
        credentials = Credentials.from_service_account_info(
            json.loads(self.credentials_json),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        spreadsheet = gspread.authorize(credentials).open_by_key(self.sheet_id)
        try:
            worksheet = spreadsheet.worksheet(self.worksheet_name)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(self.worksheet_name, rows=2000, cols=len(HEADERS))
            worksheet.append_row(HEADERS, value_input_option="RAW")
        return worksheet

    def append_order(self, order: CleanOrder, package_count: int, raw_text: str) -> str:
        if not self.configured:
            return "本機測試完成（尚未設定Google試算表）"
        now = datetime.now(ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Taipei"))).strftime(
            "%Y/%m/%d %H:%M:%S"
        )
        self._worksheet().append_row(
            [
                now, "LINE-Codex", order.customer_name, order.recipient_name,
                order.recipient_phone, order.recipient_address, order.five_jin_boxes,
                order.ten_jin_boxes, order.sender_name, order.sender_phone,
                order.sender_address, order.note, order.receipt_note,
                f"{order.confidence:.2f}", package_count, "待產生黑貓CSV", raw_text,
            ],
            value_input_option="RAW",
        )
        return "已寫入Google測試表"
