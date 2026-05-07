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
    combined = text.strip()
    lines = [l.strip() for l in combined.splitlines() if l.strip()]

    # 從全文找電話
    phone_match = re.search(r"0\d[\d\-]{8,10}", combined.replace(" ", ""))
    phone = fix_phone(phone_match.group()) if phone_match else None

    # 從全文找數量
    q5 = re.search(r"5斤[：:\-]?\s*(\d+)\s*箱", combined)
    q10 = re.search(r"10斤[：:\-]?\s*(\d+)\s*箱", combined)
    qty5 = int(q5.group(1)) if q5 else 0
    qty10 = int(q10.group(1)) if q10 else 0

    # 從各行找地址（含縣市）
    addr = ""
    for line in lines:
        if re.search(r"(台|臺|高|新|桃|苗|彰|南|嘉|屏|宜|花|東|基|雲|澎|金|連).{1,3}(市|縣)", line):
            addr = line
            break

    # 找姓名：逐行排除電話行、地址行、數量行，剩下第一個純中文詞
    name = ""
    for line in lines:
        if phone and re.search(r"0\d[\d\-]{8,10}", line.replace(" ", "")):
            continue
        if addr and addr in line:
            continue
        if re.search(r"[5１][斤]|[10１０][斤]", line):
            continue
        if line and not name:
            name = line
            break

    # 單行輸入：從剩餘文字中提取姓名
    if not name:
        remaining = combined
        if phone_match:
            remaining = remaining.replace(phone_match.group(), " ")
        if addr:
            remaining = remaining.replace(addr, " ")
        if q5:
            remaining = remaining.replace(q5.group(), " ")
        if q10:
            remaining = remaining.replace(q10.group(), " ")
        for token in re.split(r'[\s　]+', remaining):
            token = token.strip()
            if re.match(r'^[一-鿿]{1,5}$', token):
                name = token
                break

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
        sheet = get_sheet()
        now = datetime.now(TZ).strftime("%Y/%m/%d %H:%M:%S")
        row = [
            now,
            order["name"],
            order["phone"],
            "LINE訂單",
            "",
            "",
            order["addr"],
            str(order["qty5"]) if order["qty5"] > 0 else "",
            str(order["qty10"]) if order["qty10"] > 0 else "",
            "",
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
        else:
            reply = (
                "⚠️ 無法辨識訂單格式\n\n"
                "請包含以下四項：\n"
                "・姓名\n"
                "・電話（0開頭10碼）\n"
                "・地址（含縣市）\n"
                "・數量（5斤幾箱 / 10斤幾箱）\n\n"
                "範例：\n"
                "王小明\n"
                "0912345678\n"
                "台北市信義區松仁路100號\n"
                "5斤2箱 10斤1箱"
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
