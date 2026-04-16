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
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]
SHEET_ID = os.environ["SHEET_ID"]

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    return spreadsheet.worksheet("表單回覆1")

VALID = [
    {"s5": 4, "s10": 0, "label": "5斤4箱"},
    {"s5": 2, "s10": 1, "label": "5斤2+10斤1"},
    {"s5": 1, "s10": 1, "label": "5斤1+10斤1"},
    {"s5": 0, "s10": 2, "label": "10斤2箱"},
    {"s5": 2, "s10": 0, "label": "5斤2箱"},
    {"s5": 1, "s10": 0, "label": "5斤1箱"},
    {"s5": 0, "s10": 1, "label": "10斤1箱"},
]

def best_split(s5, s10):
    memo = {}
    def dp(r5, r10):
        key = (r5, r10)
        if key in memo:
            return memo[key]
        if r5 == 0 and r10 == 0:
            return {"cost": 0, "shipments": []}
        costs = {
            (4,0):200,(2,1):200,(1,1):200,(0,2):200,
            (2,0):180,(1,0):140,(0,1):140
        }
        best = {"cost": 999999, "shipments": None}
        for v in VALID:
            if v["s5"] <= r5 and v["s10"] <= r10:
                sub = dp(r5 - v["s5"], r10 - v["s10"])
                cost = costs[(v["s5"], v["s10"])] + sub["cost"]
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
    phone_match = re.search(r"0\d[\d\-]{8,10}", text)
    if not phone_match:
        return None

    phone = fix_phone(phone_match.group())

    qty5_match = re.search(r"5斤[：:]?\s*(\d+)\s*箱", text)
    qty10_match = re.search(r"10斤[：:]?\s*(\d+)\s*箱", text)
    qty5 = int(qty5_match.group(1)) if qty5_match else 0
    qty10 = int(qty10_match.group(1)) if qty10_match else 0

    if qty5 == 0 and qty10 == 0:
        return None

    remainder = text[:phone_match.start()].strip()
    tokens = remainder.split()
    name = tokens[0] if tokens else ""

    after_phone = text[phone_match.end():].strip()
    after_phone = re.sub(r"\d+斤[：:]?\s*\d+\s*箱", "", after_phone).strip()
    addr = after_phone if after_phone else ""

    if not name or not addr:
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
        sheet = get_sheet()
        now = datetime.now(TZ).strftime("%Y/%m/%d %H:%M:%S")
        row = [
            now,                                                    # A 時間戳記
            order["name"],                                          # B 訂購人姓名
            order["phone"],                                         # C 訂購人電話
            "LINE訂單",                                             # D Line暱稱
            "",                                                     # E 收貨人姓名
            "",                                                     # F 收貨人電話
            order["addr"],                                          # G 收貨地址
            str(order["qty5"]) if order["qty5"] > 0 else "",       # H 5斤數量
            str(order["qty10"]) if order["qty10"] > 0 else "",     # I 10斤數量
            "",                                                     # J 備註
        ]
        sheet.append_row(row)
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
        if order:
            result = best_split(order["qty5"], order["qty10"])
            ships = " + ".join([s["label"] for s in result["shipments"]])
            success = write_to_sheet(order)
            sheet_status = "已記錄到表單 ✅" if success else "⚠️ 表單記錄失敗"
            reply = (
                f"✅ 已收到訂單：\n"
                f"{order['name']} / {order['phone']}\n"
                f"{order['addr']}\n"
                f"分件：{ships}（運費${result['cost']}）\n"
                f"{sheet_status}"
            )
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token,
                                    messages=[TextMessage(text=reply)])
            )

@app.route("/", methods=["GET"])
def health():
    return "Line Bot running."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
