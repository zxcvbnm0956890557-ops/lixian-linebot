import os
import re
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)
TZ = ZoneInfo("Asia/Taipei")

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GOOGLE_CREDENTIALS = os.environ["GOOGLE_CREDENTIALS"]
SHEET_ID = "16ekyKXYK_anAGD3LbipriX4ZSzEcS5CCljapxy0CBfw"
WORKSHEET_NAME = "LINE訂單"

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

VALID = [
    {"s5": 4, "s10": 0, "cost": 200, "label": "5斤4箱"},
    {"s5": 2, "s10": 1, "cost": 200, "label": "5斤2+10斤1"},
    {"s5": 1, "s10": 1, "cost": 200, "label": "5斤1+10斤1"},
    {"s5": 0, "s10": 2, "cost": 200, "label": "10斤2箱"},
    {"s5": 2, "s10": 0, "cost": 180, "label": "5斤2箱"},
    {"s5": 1, "s10": 0, "cost": 140, "label": "5斤1箱"},
    {"s5": 0, "s10": 1, "cost": 140, "label": "10斤1箱"},
]

HEADERS = ["時間戳記", "收貨人姓名", "收貨人電話", "收貨地址", "5斤幾箱", "10斤幾箱", "備註", "狀態"]


def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    try:
        ws = spreadsheet.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=WORKSHEET_NAME, rows=1000, cols=10)
        ws.append_row(HEADERS)
        ws.format("C:C", {"numberFormat": {"type": "TEXT"}})
    return ws


def best_split(s5, s10):
    memo = {}
    def dp(r5, r10):
        key = (r5, r10)
        if key in memo:
            return memo[key]
        if r5 == 0 and r10 == 0:
            return {"cost": 0, "shipments": []}
        best = {"cost": 999999, "shipments": None}
        for v in VALID:
            if v["s5"] <= r5 and v["s10"] <= r10:
                sub = dp(r5 - v["s5"], r10 - v["s10"])
                cost = v["cost"] + sub["cost"]
                if cost < best["cost"]:
                    best = {"cost": cost, "shipments": [v] + sub["shipments"]}
        memo[key] = best
        return best
    return dp(s5, s10)


def fix_phone(p):
    s = re.sub(r"[^0-9]", "", str(p).strip())
    while len(s) < 10:
        s = "0" + s
    return s


def parse_order(text):
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]

    phone = None
    qty5 = 0
    qty10 = 0
    name = ""
    addr = ""

    for line in lines:
        if re.match(r"^0\d[\d\-]{8,10}$", line.replace("-", "").replace(" ", "")):
            phone = fix_phone(line)
            continue

        has_qty = False
        q5 = re.search(r"5斤[：:]?\s*(\d+)\s*箱", line)
        q10 = re.search(r"10斤[：:]?\s*(\d+)\s*箱", line)
        if q5:
            qty5 = int(q5.group(1))
            has_qty = True
        if q10:
            qty10 = int(q10.group(1))
            has_qty = True
        if has_qty:
            continue

        if re.search(r"(台|臺|高|新|桃|苗|彰|南|嘉|屏|宜|花|東|基|雲|澎|金|連).{1,3}(市|縣)", line):
            addr = line
            continue

        if not name:
            name = line

    if not phone:
        combined = " ".join(lines)
        phone_match = re.search(r"0\d[\d\-]{8,10}", combined)
        if phone_match:
            phone = fix_phone(phone_match.group())

    if not phone or (qty5 == 0 and qty10 == 0) or not name or not addr:
        return None

    return {
        "name": name,
        "phone": phone,
        "addr": addr,
        "qty5": qty5,
        "qty10": qty10,
    }


def write_to_sheet(order):
    try:
        ws = get_sheet()
        now = datetime.now(TZ).strftime("%Y/%m/%d %H:%M:%S")
        row = [
            now,
            order["name"],
            order["phone"],
            order["addr"],
            f"5斤：{order['qty5']}箱",
            f"10斤：{order['qty10']}箱",
            "",
            "",
        ]
        ws.append_row(row, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        print(f"寫入試算表失敗：{e}")
        return False


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        order = parse_order(text)
        if not order:
            return

        result = best_split(order["qty5"], order["qty10"])
        ships = " + ".join([s["label"] for s in result["shipments"]])
        success = write_to_sheet(order)
        sheet_status = "已記錄到試算表 ✅" if success else "⚠️ 試算表記錄失敗"
        reply = (
            f"✅ 已收到訂單：\n"
            f"{order['name']} / {order['phone']}\n"
            f"{order['addr']}\n"
            f"分件：{ships}（運費${result['cost']}）\n"
            f"{sheet_status}"
        )
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)]
            )
        )


@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


@app.route("/", methods=["GET"])
def index():
    return "LINE Bot running.", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
