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


def extract_addr(line):
    """從一行文字中抽取地址（含縣市關鍵字）"""
    clean = re.sub(r'^(地址|收貨住址|收件地址|寄送地址|配送地址)[：:]\s*', '', line)
    m = re.search(r'(台|臺|高|新|桃|苗|彰|南|嘉|屏|宜|花|東|基|雲|澎|金|連)\S*(市|縣)\S+', clean)
    return m.group() if m else None


def parse_qty(text):
    """從文字中解析 5斤 和 10斤 箱數，回傳 (qty5, qty10)"""
    q5 = re.search(r'5斤[裝装]?[：:+＋\-]?\s*(\d+)\s*[箱個]?', text)
    q10 = re.search(r'10斤[裝装]?[：:+＋\-]?\s*(\d+)\s*[箱個]?', text)
    return int(q5.group(1)) if q5 else 0, int(q10.group(1)) if q10 else 0


def parse_daigou_blocks(text):
    """
    解析「代購分寄」格式，每個區塊為：
      數量行（結尾有 ：或 :）
      地址行
      收件人姓名+電話（可能連在一起，或分兩行）
      （備注）
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # 找所有「含斤數且結尾是冒號」的行（每個區塊起點）
    block_starts = []
    for i, line in enumerate(lines):
        if re.search(r'[5５]斤|10斤', line) and re.search(r'[：:]\s*$', line):
            block_starts.append(i)

    if not block_starts:
        return []

    orders = []
    for k, start in enumerate(block_starts):
        end = block_starts[k + 1] if k + 1 < len(block_starts) else len(lines)
        block = lines[start:end]
        qty_line = block[0]
        rest = block[1:]

        # 解析數量
        qty_line_clean = re.sub(r'[：:]\s*$', '', qty_line)
        qty5, qty10 = parse_qty(qty_line_clean)
        if qty5 == 0 and qty10 == 0:
            continue

        # 找地址
        addr = ""
        addr_idx = -1
        for i, line in enumerate(rest):
            a = extract_addr(line)
            if a:
                addr = line  # 保留整行（含郵遞區號）
                addr_idx = i
                break

        # 找備注（括號包起來的行）
        note = ""
        for i, line in enumerate(rest):
            if i == addr_idx:
                continue
            if re.search(r'[（(【].*[）)】]', line) or re.match(r'^[（(【]', line):
                note = re.sub(r'^[（(【\s]+|[）)】\s]+$', '', line).strip()

        # 找電話和姓名
        phone = None
        name = ""
        for i, line in enumerate(rest):
            if i == addr_idx:
                continue
            if re.search(r'[（(【]', line) and note and note in line:
                continue  # 備注行跳過

            raw = line.replace(" ", "").replace("　", "")

            # 姓名（2-8字中文）+電話 連在一起
            m = re.search(r'([一-鿿]{2,8})(0\d{9,10})', raw)
            if m:
                name = m.group(1)
                phone = fix_phone(m.group(2))
                break

            # 姓名/電話 或 姓名 電話
            m2 = re.match(r'^([一-鿿\w]{2,8})\s*[/／\s]\s*(0\d[\d\-]{8,10})', line)
            if m2:
                name = m2.group(1)
                phone = fix_phone(m2.group(2))
                break

            # 純電話行
            m3 = re.search(r'0\d[\d\-]{8,10}', raw)
            if m3:
                phone = fix_phone(m3.group())
                before = raw[:raw.index(m3.group())]
                name_part = re.sub(r'[^一-鿿]', '', before)
                if re.match(r'^[一-鿿]{2,8}$', name_part):
                    name = name_part
                break

        if phone and addr and (qty5 > 0 or qty10 > 0):
            orders.append({
                "name": name or "未填姓名",
                "phone": phone,
                "addr": addr,
                "qty5": qty5,
                "qty10": qty10,
                "note": note,
            })

    return orders


def parse_order(text):
    combined = text.strip()
    lines = [l.strip() for l in combined.splitlines() if l.strip()]

    phone_match = re.search(r"0\d[\d\-]{8,10}", combined.replace(" ", ""))
    phone = fix_phone(phone_match.group()) if phone_match else None

    qty5, qty10 = parse_qty(combined)

    addr = ""
    for line in lines:
        a = extract_addr(line)
        if a:
            addr = a
            break

    name = ""
    candidates = []
    for line in lines:
        if re.search(r'[【】]', line):
            continue
        if phone and re.search(r"0\d[\d\-]{8,10}", line.replace(" ", "")):
            # 同行可能有姓名（姓名+電話連在一起）
            raw = line.replace(" ", "")
            m = re.search(r'([一-鿿]{2,8})(0\d{9,10})', raw)
            if m and not name:
                name = m.group(1)
            continue
        if addr and (addr in line or re.search(r"(台|臺|高|新|桃|苗|彰|南|嘉|屏|宜|花|東|基|雲|澎|金|連).{1,3}(市|縣)", line)):
            continue
        if re.search(r"[5１][斤]|[10１０][斤]", line):
            continue
        clean = re.sub(r'^(訂購人|姓名|收件人|購買人)[：:]\s*', '', line)
        clean = re.sub(r'[（(].*', '', clean).strip()
        if clean:
            candidates.append(clean)

    if not name:
        for c in candidates:
            if re.match(r'^[一-鿿]{2,4}$', c):
                name = c
                break
        if not name and candidates:
            name = candidates[0]

    if not name:
        remaining = combined
        if phone_match:
            remaining = remaining.replace(phone_match.group(), " ")
        if addr:
            remaining = remaining.replace(addr, " ")
        for token in re.split(r'[\s　]+', remaining):
            token = token.strip()
            if re.match(r'^[一-鿿]{1,5}$', token):
                name = token
                break

    if not phone or (qty5 == 0 and qty10 == 0) or not name or not addr:
        return None

    return {"name": name, "phone": phone, "addr": addr, "qty5": qty5, "qty10": qty10, "note": ""}


def parse_multi_orders(text):
    """處理各種格式的訂單，拆成多筆 order dict。"""
    combined = text.strip()
    non_empty = [(i, l.strip()) for i, l in enumerate(combined.splitlines()) if l.strip()]

    # === 格式A：代購分寄（含「訂購人：」或數量行結尾有「：」）===
    has_daigou = (
        any(re.match(r'^(訂購人|購買人)[：:]', l) for _, l in non_empty) or
        any(re.search(r'[5５]斤|10斤', l) and re.search(r'[：:]\s*$', l) for _, l in non_empty)
    )
    if has_daigou:
        orders = parse_daigou_blocks(combined)
        if orders:
            return orders

    # === 格式B：收件人標籤（收件人：姓名 緊接在數量行後）===
    block_starts = []
    for k, (idx, line) in enumerate(non_empty):
        if re.search(r'[5５][斤]|10斤', line):
            if k + 1 < len(non_empty) and re.match(r'收件人[：:]', non_empty[k + 1][1]):
                block_starts.append(idx)

    if len(block_starts) > 1:
        orders = []
        for k, start in enumerate(block_starts):
            end = block_starts[k + 1] if k + 1 < len(block_starts) else float('inf')
            block_lines = [l for i, l in non_empty if start <= i < end]
            order = parse_order('\n'.join(block_lines))
            if order:
                orders.append(order)
        return orders

    # === fallback：單筆或其他格式 ===
    order = parse_order(combined)
    return [order] if order else []


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
            order.get("note", ""),
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
                note_line = f"\n   備注：{o['note']}" if o.get("note") else ""
                parts.append(
                    f"\n{i}. {o['name']} / {o['phone']}\n"
                    f"   {o['addr']}\n"
                    f"   {ships}（${res['cost']}）{note_line}"
                )
            parts.append("\n全部記錄到試算表 ✅" if all_ok else "\n⚠️ 部分試算表記錄失敗")
            reply = "\n".join(parts)

        elif len(orders) == 1:
            order = orders[0]
            result = best_split(order["qty5"], order["qty10"])
            ships = " + ".join([s["label"] for s in result["shipments"]])
            success = write_to_sheet(order)
            sheet_status = "已記錄到試算表 ✅" if success else "⚠️ 試算表記錄失敗"
            note_line = f"\n備注：{order['note']}" if order.get("note") else ""
            reply = (
                f"✅ 已收到訂單：\n"
                f"{order['name']} / {order['phone']}\n"
                f"{order['addr']}\n"
                f"分件：{ships}（運費${result['cost']}）{note_line}\n"
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
