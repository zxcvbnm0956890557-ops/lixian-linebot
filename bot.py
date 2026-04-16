import os
import re
import json
import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, GroupSource

app = Flask(__name__)
TZ = ZoneInfo("Asia/Taipei")

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GROUP_ID = os.environ.get("LINE_GROUP_ID", "")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# In-memory 訂單儲存
orders = []

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
    """解析訂單文字，格式：姓名 電話 地址 5斤N箱 10斤N箱"""
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
    # 移除數量資訊
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
        "date": datetime.now(TZ).strftime("%Y-%m-%d"),
    }

def generate_csv():
    rows = ["收件人姓名,收件人手機,收件人地址,備註,寄件人姓名,寄件人手機"]
    for o in orders:
        result = best_split(o["qty5"], o["qty10"])
        for ship in result["shipments"]:
            rows.append(",".join([
                f'"{o["name"]}"',
                f'"{o["phone"]}"',
                f'"{o["addr"]}"',
                f'"{ship["label"]}"',
                '""',
                '""',
            ]))
    return "\n".join(rows)

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
    
    # 只處理群組訊息
    if not isinstance(event.source, GroupSource):
        return

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        # 指令：列表
        if text in ("/list", "列表", "今日訂單"):
            if not orders:
                reply = "今天還沒有手動訂單"
            else:
                lines = [f"{i+1}. {o['name']} / {o['phone']} / 5斤{o['qty5']}+10斤{o['qty10']}" 
                         for i, o in enumerate(orders)]
                reply = f"今日手動訂單（{len(orders)}筆）：\n" + "\n".join(lines)
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token,
                                    messages=[TextMessage(text=reply)])
            )
            return

        # 指令：清除
        if text in ("/clear", "清除"):
            orders.clear()
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token,
                                    messages=[TextMessage(text="✅ 已清除所有手動訂單")])
            )
            return

        # 解析訂單
        order = parse_order(text)
        if order:
            orders.append(order)
            result = best_split(order["qty5"], order["qty10"])
            ships = " + ".join([s["label"] for s in result["shipments"]])
            reply = f"✅ 已收到訂單：\n{order['name']} / {order['phone']}\n{order['addr']}\n分件：{ships}（運費${result['cost']}）"
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token,
                                    messages=[TextMessage(text=reply)])
            )

@app.route("/get-csv", methods=["GET"])
def get_csv():
    csv_content = generate_csv()
    return csv_content, 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": f"attachment; filename=manual_orders_{datetime.now(TZ).strftime('%Y%m%d')}.csv"
    }

@app.route("/", methods=["GET"])
def health():
    return f"Line Bot running. 今日手動訂單：{len(orders)}筆"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
