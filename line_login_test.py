"""Owner-only LINE Login and disposable receipt trial; no production orders."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import closing
from urllib.parse import urlencode, urlparse

import requests
from flask import Blueprint, make_response, redirect, render_template_string, request

line_login_test = Blueprint("line_login_test", __name__, url_prefix="/line-login-test")
ORIGIN = "https://lixian-linebot.onrender.com"
CALLBACK = ORIGIN + "/line-login-test/callback"
COOKIE = "__Secure-lixian-trial"
PATH = "/line-login-test"
SITE = "https://lixian-passion-fruit.zxcvbnm0956890557.chatgpt.site"
TEST_SHEET_ID = "1B7qYGRgJQuUfqdwdwgyzUhSzh6i4u4pe8pgVb4edBcI"


def _save_test_sheet(receipt, profile):
    """Only the explicitly approved test workbook; never reuse GOOGLE_SHEET_ID."""
    if os.getenv('LINE_LOGIN_TEST_SHEET_ENABLED') != '1':
        return '未啟用 Google 測試表寫入。'
    import gspread
    from google.oauth2.service_account import Credentials
    credentials = Credentials.from_service_account_info(
        json.loads(os.getenv('GOOGLE_CREDENTIALS_JSON') or os.getenv('GOOGLE_CREDENTIALS', '')),
        scopes=['https://www.googleapis.com/auth/spreadsheets'])
    book = gspread.authorize(credentials).open_by_key(TEST_SHEET_ID)
    orders = book.worksheet('Form Responses 1')
    headers = orders.row_values(1)
    if len(headers) != 11 or headers[0:2] != ['Column 11', 'Timestamp'] or headers[8:10] != ['5斤百香果 $650/箱', '10斤百香果 $1250/箱']:
        raise ValueError('test sheet schema changed')
    binding_headers = ['測試訂單編號', '寫入時間', '訂單列', 'LINE暱稱', 'LINE帳號識別', '5斤箱數', '10斤箱數', '商品小計', '運費', '含運總額']
    try:
        binding = book.worksheet('LINE綁定測試')
    except gspread.WorksheetNotFound:
        binding = book.add_worksheet('LINE綁定測試', rows=200, cols=10)
        binding.append_row(binding_headers, value_input_option='RAW')
    if binding.row_values(1) != binding_headers:
        raise ValueError('binding sheet schema changed')
    order = receipt['order']
    now = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y/%m/%d %H:%M:%S')
    same = order['same'] == '1'
    result = orders.append_row([
        '', now, '測試勿出貨—' + order['name'], order['phone'], order['address'] if same else order['recipient_address'],
        profile['name'], '收貨人就是我：訂購人本人' if same else order['recipient_name'],
        '收貨人就是我：訂購人本人' if same else order['recipient_phone'],
        f"5斤：{order['five']}箱", f"10斤：{order['ten']}箱", order['note']], value_input_option='RAW')
    row_range = result.get('updates', {}).get('updatedRange', '')
    # Keep this reference even if the second write fails. Never retry a possibly
    # accepted append automatically: a timeout is not proof that it was rejected.
    receipt['sheet_order_range'] = row_range
    # Preserve the existing native table's dropdowns/column rules when its
    # previous range ended before the newly appended row. Never recreate it.
    match = re.search(r'!A(\d+):K\d+$', row_range)
    if match:
        end_row = int(match.group(1))
        metadata = book.fetch_sheet_metadata()
        repairs = []
        for sheet in metadata.get('sheets', []):
            if sheet.get('properties', {}).get('title') != 'Form Responses 1':
                continue
            for table in sheet.get('tables', []):
                area = table.get('range', {})
                if area.get('startRowIndex', 0) == 0 and area.get('startColumnIndex', 0) == 0 and area.get('endColumnIndex') == 11 and area.get('endRowIndex', 0) < end_row:
                    repairs.append({'updateTable': {'table': {'tableId': table['tableId'], 'range': {**area, 'endRowIndex': end_row}}, 'fields': 'range'}})
        if repairs:
            book.batch_update({'requests': repairs})
    binding.append_row([receipt['id'], now, row_range, profile['name'],
                        os.environ['LINE_LOGIN_TEST_OWNER_ID'], order['five'], order['ten'],
                        order['subtotal'], order['shipping'], order['total']], value_input_option='RAW')
    return '已寫入 Google 測試表及 LINE 綁定紀錄。'
PAGE = """<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>李鮮 LINE 登入測試</title>
<style>body{margin:0;background:#fbf7ec;color:#173f30;font:18px/1.7 system-ui,sans-serif}main{max-width:560px;margin:8vh auto;padding:28px}h1{font-size:30px;line-height:1.3}p{margin:18px 0}button,a.action{font:inherit;display:inline-block;background:#06c755;color:#fff;border:0;border-radius:8px;padding:14px 24px;text-decoration:none;cursor:pointer}small{display:block;color:#665b62;font-size:16px}img{width:88px;height:88px;border-radius:50%;object-fit:cover}hr{border:0;border-top:1px solid #d8d5c9;margin:28px 0}button.secondary{background:#173f30}label{display:block;margin:16px 0}input:not([type=hidden]):not([type=checkbox]),select{display:block;box-sizing:border-box;width:100%;padding:12px;border:1px solid #8b968b;background:white;font:inherit}input[type=checkbox]{width:24px;height:24px;vertical-align:middle}fieldset{border:0;padding:0;margin:24px 0}legend{font-weight:bold}#total{font-weight:bold;color:#6a3550}button:disabled{opacity:.6} .error{color:#9c2134}</style>
<main><small>李鮮百香果 · 僅供本人測試</small><h1>{{ title }}</h1><p role="status">{{ message }}</p>
{% if profile %}{% if profile.picture %}<img src="{{ profile.picture }}" alt="你的 LINE 頭像">{% endif %}<h2>{{ profile.name }}</h2><p>已驗證 LINE 帳號 · 尾碼 {{ profile.tail }}</p><p>官方 LINE 好友狀態：{{ profile.friend }}</p><hr><h2>測試結帳</h2><small>請用測試資料填寫。不收款、不配送、不寫入正式訂單表；僅寫入指定測試表。送單後自動通知你的 LINE，不必另外按通知。</small>
{% if receipt %}<h2>測試訂單已儲存</h2><p>5 斤 × {{ receipt.order.five }} 箱／10 斤 × {{ receipt.order.ten }} 箱</p><p>商品 NT${{ receipt.order.subtotal }}＋運費 NT${{ receipt.order.shipping }}＝NT${{ receipt.order.total }}</p><p role="status">{{ receipt.sheet_status }}</p><p role="status">{{ receipt.status }}</p><small>測試編號：{{ receipt.id }}。此輪測試 24 小時內不再發送，避免重複通知。伺服器重新部署會清除暫存紀錄。</small>{% elif message_ready %}
{% if errors %}<div class="error" role="alert">{% for error in errors %}<p>{{ error }}</p>{% endfor %}</div>{% endif %}
<form id="checkout" method="post" action="/line-login-test/test-order"><input type="hidden" name="csrf" value="{{ csrf }}">
<fieldset><legend>1. 選擇箱數</legend><label>5 斤百香果 · NT$650／箱<select name="five" id="five">{% for n in range(41) %}{% if n != 3 %}<option value="{{ n }}" {% if values.get('five','1') == n|string %}selected{% endif %}>{{ n }} 箱</option>{% endif %}{% endfor %}</select></label><label>10 斤百香果 · NT$1,250／箱<select name="ten" id="ten">{% for n in range(41) %}<option value="{{ n }}" {% if values.get('ten','0') == n|string %}selected{% endif %}>{{ n }} 箱</option>{% endfor %}</select></label></fieldset>
<fieldset><legend>2. 填寫資料</legend><label>訂購人姓名（必填）<input name="name" maxlength="40" required value="{{ values.get('name','') }}" placeholder="例：測試本人"></label><label>電話（必填）<input name="phone" inputmode="tel" maxlength="20" required value="{{ values.get('phone','') }}" placeholder="可用測試號碼 0900000000"></label><label>地址（必填）<input name="address" maxlength="200" required value="{{ values.get('address','') }}" placeholder="可填：測試地址，請勿出貨"></label><p>LINE 暱稱已由登入取得，不用再填。</p><label><input type="checkbox" id="same" name="same" value="1" {% if values.get('same','1') == '1' %}checked{% endif %}> 自己吃，收貨人就是我</label><small>取消勾選＝送禮，請填寫對方收貨資料。</small><fieldset id="recipient"><legend>送禮收貨人</legend><label>收貨人姓名<input name="recipient_name" maxlength="40" value="{{ values.get('recipient_name','') }}"></label><label>收貨人電話<input name="recipient_phone" inputmode="tel" maxlength="20" value="{{ values.get('recipient_phone','') }}"></label><label>收貨人地址<input name="recipient_address" maxlength="200" value="{{ values.get('recipient_address','') }}"></label></fieldset><label>出貨備註（選填，限 10 字）<input name="note" id="note" maxlength="10" value="{{ values.get('note','') }}"><small id="note-count">0/10 字</small></label></fieldset>
<fieldset><legend>3. 確認並送出</legend><p id="total" aria-live="polite">送出時依箱數計算含運金額。</p><button id="submit">確認送出測試訂單</button><small>訂單先存入測試區，再自動傳送 LINE；通知失敗不會刪除訂單。</small></fieldset></form><script src="/line-login-test/checkout.js" defer></script>
{% else %}<p>通知設定尚未完成，暫時不能送出。</p>{% endif %}
<hr><small>登入 15 分鐘後失效；通知測試紀錄保留 24 小時防止重複送出，過期後於下次存取時清除。結束測試可立即清除暱稱及頭像，不影響已傳送的 LINE 訊息。</small><form method="post" action="/line-login-test/logout"><input type="hidden" name="csrf" value="{{ csrf }}"><p><button class="secondary">結束測試並清除登入資料</button></p></form>
{% elif ready %}<form method="post" action="/line-login-test/start"><input type="hidden" name="csrf" value="{{ csrf }}"><button>使用 LINE 登入</button></form><p><small>接著會前往 LINE 本身的登入及授權畫面。只讀取暱稱、頭像與帳號識別；不取得電話、地址或密碼。加入官方 LINE 由你自行決定。</small></p>
{% else %}<a class="action" href="/line-login-test">回到測試頁</a>{% endif %}<hr><small>這是獨立測試，正式訂購網站維持不變。</small></main></html>"""


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _db():
    db = sqlite3.connect(os.getenv("LINE_LOGIN_TEST_DB", "/tmp/lixian-line-login-test.sqlite3"), timeout=5)
    db.execute("PRAGMA secure_delete=ON")
    db.execute("CREATE TABLE IF NOT EXISTS trial (key TEXT PRIMARY KEY, kind TEXT, expires INTEGER, payload TEXT)")
    db.execute("DELETE FROM trial WHERE expires <= ?", (int(time.time()),))
    db.commit()
    return db


def _put(key, kind, payload, ttl):
    with closing(_db()) as db, db:
        if db.execute("SELECT count(*) FROM trial").fetchone()[0] >= 500:
            raise ValueError("trial capacity")
        db.execute("INSERT INTO trial VALUES (?,?,?,?)", (_hash(key), kind, int(time.time()) + ttl, json.dumps(payload)))


def _get(key, kind, consume=False):
    with closing(_db()) as db, db:
        if consume:
            db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT payload FROM trial WHERE key=? AND kind=?", (_hash(key), kind)).fetchone()
        if consume:
            db.execute("DELETE FROM trial WHERE key=? AND kind=?", (_hash(key), kind))
        return json.loads(row[0]) if row else None


def _ready():
    return (os.getenv("LINE_LOGIN_TEST_ENABLED") == "1" and
            bool(os.getenv("LINE_LOGIN_TEST_CHANNEL_ID")) and
            len(os.getenv("LINE_LOGIN_TEST_CHANNEL_SECRET", "")) >= 32 and
            bool(os.getenv("LINE_LOGIN_TEST_OWNER_ID")))


def _page(title, message, status=200, **kw):
    return make_response(render_template_string(PAGE, title=title, message=message, **kw), status)


@line_login_test.after_request
def protect(response):
    response.headers["Cache-Control"] = "no-store"
    # Keep Origin on same-origin form POSTs for CSRF validation, but send no
    # referrer to LINE or any other external destination.
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src https://*.line-scdn.net; form-action 'self' https://access.line.me; frame-ancestors 'none'; base-uri 'none'"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@line_login_test.get("")
def home():
    if not _ready():
        return _page("登入測試尚未開放", "設定完成後才能開始測試。", 503)
    token = request.cookies.get(COOKIE, "")
    profile = _get(token, "session") if token else None
    cart = None
    if 'five' in request.args or 'ten' in request.args:
        try:
            five, ten = int(request.args.get('five', '0')), int(request.args.get('ten', '0'))
            if not 0 <= five <= 40 or not 0 <= ten <= 40 or five == 3 or five + ten == 0:
                raise ValueError()
            cart = {'five': str(five), 'ten': str(ten)}
        except ValueError:
            return _page('箱數不符合測試配送規格', '請回原網站重新選擇箱數。', 400)
    if profile:
        return _owner_page(profile, token, values=cart or profile.get('cart'))
    token = secrets.token_urlsafe(32)
    if cart:
        try:
            _put(token, 'cart', cart, 900)
        except ValueError:
            return _page('請稍後再試', '目前測試請求較多。', 429)
    message = (f"已帶入原網站的箱數：5斤 {cart['five']}箱、10斤 {cart['ten']}箱。登入後填寫資料，送單就會自動通知 LINE。" if cart else "登入後可選箱數、填寫資料，送出測試訂單就會自動通知 LINE。")
    response = _page("使用 LINE 登入結帳", message, ready=True, csrf=token)
    response.set_cookie(COOKIE, token, max_age=900, secure=True, httponly=True, samesite="Lax", path=PATH)
    return response


def _csrf_ok():
    token = request.cookies.get(COOKIE, "")
    return (request.headers.get("Origin") == ORIGIN and len(token) >= 40 and
            hmac.compare_digest(token, request.form.get("csrf", "")))


def _receipt_key():
    return "checkout-v2:" + os.environ["LINE_LOGIN_TEST_OWNER_ID"]


def _owner_page(profile, token, status=200, errors=None, values=None):
    return _page("LINE 登入成功", "以下資料由 LINE 驗證，不用手動填寫暱稱。", status,
                 profile=profile, csrf=token, receipt=_get(_receipt_key(), "receipt"),
                 message_ready=bool(os.getenv("LINE_LOGIN_TEST_MESSAGE_TOKEN")), errors=errors, values=values if values is not None else profile.get('cart', {}))


def _shipping(five, ten):
    free_eligible = five + ten >= 20
    cost = 0
    for f, t, price, full in [(4, 0, 200, True), (0, 2, 200, True), (2, 1, 200, True),
                              (1, 1, 200, False), (2, 0, 180, False), (1, 0, 140, False), (0, 1, 140, False)]:
        count = min(five // f if f else 1000, ten // t if t else 1000)
        five -= count * f
        ten -= count * t
        cost += 0 if free_eligible and full else count * price
    return cost


@line_login_test.get('/checkout.js')
def checkout_script():
    response = make_response("""const form=document.getElementById('checkout');
if(form){const update=()=>{let f=Number(form.five.value),t=Number(form.ten.value),sub=f*650+t*1250,cost=0,free=f+t>=20;
for(const [a,b,p,full] of [[4,0,200,true],[0,2,200,true],[2,1,200,true],[1,1,200,false],[2,0,180,false],[1,0,140,false],[0,1,140,false]]){const n=Math.min(a?Math.floor(f/a):1000,b?Math.floor(t/b):1000);f-=n*a;t-=n*b;cost+=free&&full?0:n*p;}
document.getElementById('total').textContent='商品 NT$'+sub.toLocaleString()+'＋運費 NT$'+cost.toLocaleString()+'＝NT$'+(sub+cost).toLocaleString();
const same=document.getElementById('same').checked;document.getElementById('recipient').hidden=same;
for(const input of document.getElementById('recipient').querySelectorAll('input'))input.required=!same;
document.getElementById('note-count').textContent=Array.from(form.note.value).length+'/10 字';};
form.addEventListener('input',update);form.addEventListener('change',update);update();
form.addEventListener('submit',()=>{const button=document.getElementById('submit');button.disabled=true;button.textContent='訂單儲存與通知中…';});}
""")
    response.headers['Content-Type'] = 'application/javascript; charset=utf-8'
    return response


def _validate_order(input_values=None):
    values = request.form.to_dict() if input_values is None else dict(input_values)
    values['same'] = '1' if values.get('same') == '1' else '0'
    errors = []
    try:
        five, ten = int(values.get('five', '')), int(values.get('ten', ''))
        if not 0 <= five <= 40 or not 0 <= ten <= 40 or five == 3 or five + ten == 0:
            raise ValueError()
    except ValueError:
        five = ten = 0
        errors.append('箱數不符合配送規格，請重新選擇；5斤不能選3箱，至少選1箱。')
    fields = [('name', '訂購人姓名', 40), ('phone', '訂購人電話', 20), ('address', '地址', 200)]
    if values['same'] != '1':
        fields += [('recipient_name', '收貨人姓名', 40), ('recipient_phone', '收貨人電話', 20), ('recipient_address', '收貨人地址', 200)]
    for key, label, limit in fields:
        values[key] = values.get(key, '').strip()
        if not values[key] or len(values[key]) > limit:
            errors.append(label + '未填寫或超過字數限制。')
        elif 'phone' in key and not re.fullmatch(r'0[0-9]{8,9}', re.sub(r'[\s-]', '', values[key])):
            errors.append(label + '格式不正確，請填寫以0開頭的9至10位電話。')
    if len(values.get('note', '')) > 10:
        errors.append('備註最多10字。')
    order = {key: values.get(key, '') for key in ['name', 'phone', 'address', 'same', 'recipient_name', 'recipient_phone', 'recipient_address', 'note']}
    order.update(five=five, ten=ten, subtotal=five * 650 + ten * 1250, shipping=_shipping(five, ten))
    order['total'] = order['subtotal'] + order['shipping']
    return order, errors, values


@line_login_test.post("/test-order")
def test_order():
    if not _ready() or not _csrf_ok():
        return _page("無法送出測試", "請重新登入後操作。", 403)
    token = request.cookies[COOKIE]
    profile = _get(token, "session")
    if not profile:
        return _page("登入已失效", "請重新登入，尚未傳送通知。", 401)
    order, errors, values = _validate_order()
    if errors:
        return _owner_page(profile, token, 422, errors, values)
    return _process_order(profile, token, order)


def _process_order(profile, token, order, site_mode=False):
    def finish(status=200):
        if site_mode:
            return make_response({'receipt': _get(_receipt_key(), 'receipt')}, status)
        return _owner_page(profile, token, status)
    if _get(_receipt_key(), 'receipt'):
        return finish()
    message_token = os.getenv("LINE_LOGIN_TEST_MESSAGE_TOKEN", "")
    if not message_token:
        return finish(503)
    headers = {"Authorization": "Bearer " + message_token}
    receipt = {"id": "TEST-" + uuid.uuid4().hex[:12].upper(),
               "status": "測試訂單已儲存，通知處理中；請勿重複送出。", "order": order}
    # Atomic, owner-wide reservation: simultaneous clicks and fresh logins cannot
    # send a second receipt. Retain failed/ambiguous requests for 24 hours too.
    try:
        _put(_receipt_key(), "receipt", receipt, 86400)
    except sqlite3.IntegrityError:
        return finish()
    except ValueError:
        return make_response({'error': '請稍後再試，尚未傳送訊息。'}, 429) if site_mode else _page("請稍後再試", "尚未傳送訊息。", 429)
    try:
        receipt['sheet_status'] = _save_test_sheet(receipt, profile)
    except Exception:
        # No credentials, contact details, or remote error bodies in logs.
        receipt['sheet_status'] = ('訂單列已寫入，但 LINE 綁定紀錄未確認，請到測試表核對。'
                                   if receipt.get('sheet_order_range') else
                                   'Google 測試表寫入結果未確認，請先核對測試表；不會自動重送。')
    text = ("【測試訂單，請勿出貨】\n我們收到你的測試訂單了。\n"
            + "編號：" + receipt["id"] + f"\n5斤百香果 × {order['five']}箱\n10斤百香果 × {order['ten']}箱\n"
            + f"商品：NT${order['subtotal']}／運費：NT${order['shipping']}\n合計：NT${order['total']}\n"
            + receipt['sheet_status'] + "\n"
            + "僅測試，不收款、不配送；未寫入正式訂單。")
    try:
        # Store the order first; a wrong OA or LINE failure must not lose it.
        info = requests.get("https://api.line.me/v2/bot/info", headers=headers, timeout=8)
        info.raise_for_status()
        if info.json().get("basicId") != "@129ntjqs":
            raise ValueError("wrong official account")
        result = requests.post("https://api.line.me/v2/bot/message/push", headers={
            **headers, "X-Line-Retry-Key": str(uuid.uuid4())}, json={
                "to": os.environ["LINE_LOGIN_TEST_OWNER_ID"],
                "messages": [{"type": "text", "text": text}]}, timeout=8)
        result.raise_for_status()
        receipt["status"] = "LINE 已接受通知請求。請打開手機確認是否收到；這不代表正式訂單成立。"
    except (requests.RequestException, ValueError, TypeError):
        receipt["status"] = "通知結果未確認，請先查看手機 LINE。為避免重複通知，不會自動重送。"
    with closing(_db()) as db, db:
        db.execute("UPDATE trial SET payload=? WHERE key=? AND kind='receipt'",
                   (json.dumps(receipt), _hash(_receipt_key())))
    return finish() if site_mode else redirect(PATH, 303)


def _site_authorized():
    secret = os.getenv('WEBSITE_NOTIFICATION_SECRET', '')
    return _ready() and bool(secret) and hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + secret)


@line_login_test.get('/site-start')
def site_start():
    if not _ready():
        return _page('測試尚未開放', '請稍後再試。', 503)
    nonce = request.args.get('nonce', '')
    if not re.fullmatch(r'[a-f0-9]{64}', nonce):
        return _page('請重新開始', '請從原網站使用 LINE 登入。', 400)
    try:
        five, ten = int(request.args.get('five', '0')), int(request.args.get('ten', '0'))
        if not 0 <= five <= 40 or not 0 <= ten <= 40 or five == 3 or five + ten == 0:
            raise ValueError()
    except ValueError:
        return _page('箱數不符合配送規格', '請回原網站重新選擇箱數。', 400)
    browser = secrets.token_urlsafe(32)
    state, line_nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    _put(state, 'flow', {'browser': _hash(browser), 'nonce': line_nonce, 'verifier': verifier, 'site_nonce': nonce, 'cart': {'five': str(five), 'ten': str(ten)}}, 300)
    response = redirect('https://access.line.me/oauth2/v2.1/authorize?' + urlencode({'response_type': 'code', 'client_id': os.environ['LINE_LOGIN_TEST_CHANNEL_ID'], 'redirect_uri': CALLBACK, 'state': state, 'nonce': line_nonce, 'scope': 'openid profile', 'bot_prompt': 'normal', 'code_challenge': challenge, 'code_challenge_method': 'S256', 'ui_locales': 'zh-TW'}), 303)
    response.set_cookie(COOKIE, browser, max_age=900, secure=True, httponly=True, samesite='Lax', path=PATH)
    return response


@line_login_test.post('/site-exchange')
def site_exchange():
    if not _site_authorized():
        return make_response({'error': '未授權'}, 403)
    data = request.get_json(silent=True) or {}
    ticket = data.get('ticket', '')
    if not isinstance(ticket, str) or not 40 <= len(ticket) <= 100:
        return make_response({'error': '登入連線失效'}, 401)
    grant = _get(ticket, 'site-ticket', consume=True)
    if not grant or not hmac.compare_digest(grant['nonce'], str(data.get('nonce', ''))):
        return make_response({'error': '登入連線失效，請重新登入'}, 401)
    return make_response({'session': grant['session']})


@line_login_test.post('/site-session')
@line_login_test.post('/site-order')
def site_session():
    if not _site_authorized():
        return make_response({'error': '未授權'}, 403)
    token = request.headers.get('X-Trial-Session', '')
    profile = _get(token, 'session') if 40 <= len(token) <= 100 else None
    if not profile:
        return make_response({'error': '登入已失效，請重新登入'}, 401)
    if request.path.endswith('/site-session'):
        return make_response({'profile': profile, 'receipt': _get(_receipt_key(), 'receipt')})
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
        return make_response({'error': '資料格式不正確'}, 400)
    order, errors, _ = _validate_order(data)
    if errors:
        return make_response({'errors': errors}, 422)
    return _process_order(profile, token, order, site_mode=True)


@line_login_test.post("/start")
def start():
    if not _ready() or not _csrf_ok():
        return _page("無法開始登入", "請回到測試頁重新開始。", 403)
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    try:
        _put(state, "flow", {"browser": _hash(request.cookies[COOKIE]), "nonce": nonce, "verifier": verifier,
                             "cart": _get(request.cookies[COOKIE], 'cart', consume=True) or {}}, 300)
    except ValueError:
        return _page("請稍後再試", "目前測試請求較多，請稍後重新開始。", 429)
    return redirect("https://access.line.me/oauth2/v2.1/authorize?" + urlencode({
        "response_type": "code", "client_id": os.environ["LINE_LOGIN_TEST_CHANNEL_ID"],
        "redirect_uri": CALLBACK, "state": state, "nonce": nonce,
        "scope": "openid profile", "prompt": "consent", "bot_prompt": "normal",
        "code_challenge": challenge, "code_challenge_method": "S256", "ui_locales": "zh-TW",
    }), 303)


@line_login_test.get("/callback")
def callback():
    if not _ready():
        return _page("測試已關閉", "無法完成登入。", 503)
    state = request.args.get("state", "")
    browser = request.cookies.get(COOKIE, "")
    flow = _get(state, "flow", consume=True) if 40 <= len(state) <= 100 else None
    if not flow or not browser or not hmac.compare_digest(flow["browser"], _hash(browser)):
        return _page("登入連線已失效", "請回到測試頁重新登入。", 400)
    if request.args.get("error"):
        return _page("你已取消登入", "沒有建立會員或訂單，可隨時重新測試。")
    code = request.args.get("code", "")
    if not code or len(code) > 2000:
        return _page("登入驗證未完成", "請重新開始。", 400)
    try:
        result = requests.post("https://api.line.me/oauth2/v2.1/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
            "client_id": os.environ["LINE_LOGIN_TEST_CHANNEL_ID"],
            "client_secret": os.environ["LINE_LOGIN_TEST_CHANNEL_SECRET"], "code_verifier": flow["verifier"],
        }, timeout=8)
        result.raise_for_status()
        tokens = result.json()
        verified = requests.post("https://api.line.me/oauth2/v2.1/verify", data={
            "id_token": tokens["id_token"], "client_id": os.environ["LINE_LOGIN_TEST_CHANNEL_ID"], "nonce": flow["nonce"],
        }, timeout=8)
        verified.raise_for_status()
        claims = verified.json()
        if (claims.get("iss") != "https://access.line.me" or
                str(claims.get("aud")) != os.environ["LINE_LOGIN_TEST_CHANNEL_ID"] or
                claims.get("nonce") != flow["nonce"] or claims.get("exp", 0) <= time.time()):
            raise ValueError("invalid identity")
        if not hmac.compare_digest(claims.get("sub", ""), os.environ["LINE_LOGIN_TEST_OWNER_ID"]):
            return _page("此測試只開放本人", "目前不開放其他客人登入，不會保存你的資料。", 403)
        friend = "尚未確認"
        try:
            friendship = requests.get("https://api.line.me/friendship/v1/status", headers={"Authorization": "Bearer " + tokens["access_token"]}, timeout=5)
            if friendship.status_code == 200:
                friend = "已加入" if friendship.json().get("friendFlag") else "未加入或已封鎖"
        except (requests.RequestException, ValueError):
            pass
        picture = claims.get("picture", "")
        host = urlparse(picture).hostname or ""
        if urlparse(picture).scheme != "https" or not host.endswith(".line-scdn.net"):
            picture = ""
        profile = {"name": claims.get("name", "LINE 使用者"), "picture": picture, "tail": claims["sub"][-4:], "friend": friend, "cart": flow.get('cart', {})}
        token = secrets.token_urlsafe(32)
        _put(token, "session", profile, 900)
        if flow.get('site_nonce'):
            ticket = secrets.token_urlsafe(32)
            _put(ticket, 'site-ticket', {'session': token, 'nonce': flow['site_nonce']}, 120)
            response = redirect(SITE + '/#line_ticket=' + ticket, 303)
            response.headers['Referrer-Policy'] = 'no-referrer'
            return response
        response = redirect(PATH, 303)
        response.set_cookie(COOKIE, token, max_age=900, secure=True, httponly=True, samesite="Lax", path=PATH)
        return response
    except (requests.RequestException, ValueError, KeyError, TypeError):
        # Never log codes, tokens, secrets, profile data or raw LINE error bodies.
        return _page("登入未完成", "LINE 驗證暫時失敗，請回到測試頁再試一次。", 502)


@line_login_test.post("/logout")
def logout():
    if not _csrf_ok():
        return _page("無法結束測試", "請回到測試頁操作。", 403)
    _get(request.cookies[COOKIE], "session", consume=True)
    response = _page("測試已結束", "登入顯示資料已清除。若有測試通知，紀錄保留 24 小時避免重複發送；沒有建立正式訂單。")
    response.delete_cookie(COOKIE, path=PATH, secure=True, httponly=True, samesite="Lax")
    return response
