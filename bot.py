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
    combined = text.strip()
    lines = [l.strip() for l in combined.splitlines() if l.strip()]

    # 從全文找電話
    phone_match = re.search(r"0\d[\d\-]{8,10}", combined.replace(" ", ""))
    phone = fix_phone(phone_match.group()) if phone_match else None

    # 從全文找數量（支援「5斤4」「5斤4箱」「5斤：4箱」「5斤裝+4」）
    q5 = re.search(r"5斤[裝装]?[：:+＋\-]?\s*(\d+)\s*[箱個]?", combined)
    q10 = re.search(r"10斤[裝装]?[：:+＋\-]?\s*(\d+)\s*[箱個]?", combined)
    qty5 = int(q5.group(1)) if q5 else 0
    qty10 = int(q10.group(1)) if q10 else 0

    # 從各行找地址（只抓縣市開頭的那段，不把整行吃進去）
    addr = ""
    for line in lines:
        clean = re.sub(r'^(地址|收貨住址|收件地址|寄送地址|配送地址)[：:]\s*', '', line)
        m = re.search(r'(台|臺|高|新|桃|苗|彰|南|嘉|屏|宜|花|東|基|雲|澎|金|連)\S*(市|縣)\S+', clean)
        if m:
            addr = m.group()
            break

    # 找姓名：排除電話行、地址行、數量行、【備注】行
    # 優先找 2-4 字純中文（人名），fallback 才取其他行
    name = ""
    candidates = []
    for line in lines:
        if re.search(r'[【】]', line):          # 【備注】直接跳過
            continue
        if phone and re.search(r"0\d[\d\-]{8,10}", line.replace(" ", "")):
            continue
        if addr and (addr in line or re.search(r"(台|臺|高|新|桃|苗|彰|南|嘉|屏|宜|花|東|基|雲|澎|金|連).{1,3}(市|縣)", line)):
            continue
        if re.search(r"[5１][斤]|[10１０][斤]", line):
            continue
        # 去除常見標籤和括號備注
        clean = re.sub(r'^(訂購人|姓名|收件人|購買人)[：:]\s*', '', line)
        clean = re.sub(r'[（(].*', '', clean).strip()
        if clean:
            candidates.append(clean)

    # 優先選 2-4 字純中文（人名特徵）
    for c in candidates:
        if re.match(r'^[一-鿿]{2,4}$', c):
            name = c
            break
    # fallback：取第一個候選
    if not name and candidates:
        name = candidates[0]

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


def parse_multi_orders(text):
    """處理一人多地址複合訂單，拆成多筆 order dict。"""
    combined = text.strip()
    non_empty = [(i, l.strip()) for i, l in enumerate(combined.splitlines()) if l.strip()]

    # 找每個「數量行 + 緊接著收件人行」的區塊起點
    block_starts = []
    for k, (idx, line) in enumerate(non_empty):
        if re.search(r'[5５][斤]|10斤', line):
            if k + 1 < len(non_empty) and re.match(r'收件人[：:]', non_empty[k + 1][1]):
                block_starts.append(idx)

    if len(block_starts) <= 1:
        order = parse_order(combined)
        return [order] if order else []

    orders = []
    for k, start in enumerate(block_starts):
        end = block_starts[k + 1] if k + 1 < len(block_starts) else float('inf')
        block_lines = [l for i, l in non_empty if start <= i < end]
        order = parse_order('\n'.join(block_lines))
        if order:
            orders.append(order)

    return orders


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

        orders = parse_multi_orders(text)

        if len(orders) > 1:
            parts = [f"✅ 收到 {len(orders)} 筆訂單："]
            all_ok = True
            for i, o in enumerate(orders, 1):
                res = best_split(o["qty5"], o["qty10"])
                ships = " + ".join([s["label"] for s in res["shipments"]])
                if not write_to_sheet(o):
                    all_ok = False
                parts.append(
                    f"\n{i}. {o['name']} / {o['phone']}\n"
                    f"   {o['addr']}\n"
                    f"   {ships}（${res['cost']}）"
                )
            parts.append("\n全部記錄到試算表 ✅" if all_ok else "\n⚠️ 部分試算表記錄失敗")
            reply = "\n".join(parts)

        elif len(orders) == 1:
            order = orders[0]
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
