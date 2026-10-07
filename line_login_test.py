"""Owner-only LINE Login and disposable receipt trial; no production orders."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from urllib.parse import urlencode, urlparse

import requests
from flask import Blueprint, make_response, redirect, render_template_string, request

line_login_test = Blueprint("line_login_test", __name__, url_prefix="/line-login-test")
ORIGIN = "https://lixian-linebot.onrender.com"
CALLBACK = ORIGIN + "/line-login-test/callback"
COOKIE = "__Secure-lixian-trial"
PATH = "/line-login-test"
PAGE = """<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>李鮮 LINE 登入測試</title>
<style>body{margin:0;background:#fbf7ec;color:#173f30;font:18px/1.7 system-ui,sans-serif}main{max-width:560px;margin:8vh auto;padding:28px}h1{font-size:30px;line-height:1.3}p{margin:18px 0}button,a.action{font:inherit;display:inline-block;background:#06c755;color:#fff;border:0;border-radius:8px;padding:14px 24px;text-decoration:none;cursor:pointer}small{display:block;color:#665b62;font-size:16px}img{width:88px;height:88px;border-radius:50%;object-fit:cover}hr{border:0;border-top:1px solid #d8d5c9;margin:28px 0}button.secondary{background:#173f30}</style>
<main><small>李鮮百香果 · 僅供本人測試</small><h1>{{ title }}</h1><p role="status">{{ message }}</p>
{% if profile %}{% if profile.picture %}<img src="{{ profile.picture }}" alt="你的 LINE 頭像">{% endif %}<h2>{{ profile.name }}</h2><p>已驗證 LINE 帳號 · 尾碼 {{ profile.tail }}</p><p>官方 LINE 好友狀態：{{ profile.friend }}</p><hr><h2>下一步：測試訂單通知</h2><p>5 斤百香果 × 1 箱 · 商品 NT$650</p><small>這是假訂單，不收款、不配送、不寫入 Google 試算表。按下後只向你本人的 LINE 傳送一則測試通知。</small>
{% if receipt %}<p role="status">{{ receipt.status }}</p><small>測試編號：{{ receipt.id }}。24 小時內不再發送，避免重複通知。</small>{% elif message_ready %}<form method="post" action="/line-login-test/test-order"><input type="hidden" name="csrf" value="{{ csrf }}"><p><button>送出測試訂單，通知我的 LINE</button></p></form>{% else %}<p>通知設定尚未完成，暫時不能送出。</p>{% endif %}
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
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src https://*.line-scdn.net; form-action 'self' https://access.line.me; frame-ancestors 'none'; base-uri 'none'"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@line_login_test.get("")
def home():
    if not _ready():
        return _page("登入測試尚未開放", "設定完成後才能開始測試。", 503)
    token = request.cookies.get(COOKIE, "")
    profile = _get(token, "session") if token else None
    if profile:
        return _owner_page(profile, token)
    token = secrets.token_urlsafe(32)
    response = _page("試試 LINE 登入", "先體驗登入與身分確認，不會成立正式訂單。", ready=True, csrf=token)
    response.set_cookie(COOKIE, token, max_age=900, secure=True, httponly=True, samesite="Lax", path=PATH)
    return response


def _csrf_ok():
    token = request.cookies.get(COOKIE, "")
    return (request.headers.get("Origin") == ORIGIN and len(token) >= 40 and
            hmac.compare_digest(token, request.form.get("csrf", "")))


def _receipt_key():
    return "receipt:" + os.environ["LINE_LOGIN_TEST_OWNER_ID"]


def _owner_page(profile, token, status=200):
    return _page("LINE 登入成功", "以下資料由 LINE 驗證，不用手動填寫暱稱。", status,
                 profile=profile, csrf=token, receipt=_get(_receipt_key(), "receipt"),
                 message_ready=bool(os.getenv("LINE_LOGIN_TEST_MESSAGE_TOKEN")))


@line_login_test.post("/test-order")
def test_order():
    if not _ready() or not _csrf_ok():
        return _page("無法送出測試", "請重新登入後操作。", 403)
    token = request.cookies[COOKIE]
    profile = _get(token, "session")
    if not profile:
        return _page("登入已失效", "請重新登入，尚未傳送通知。", 401)
    message_token = os.getenv("LINE_LOGIN_TEST_MESSAGE_TOKEN", "")
    if not message_token:
        return _owner_page(profile, token, 503)
    headers = {"Authorization": "Bearer " + message_token}
    # Fail closed if the customer OA token was confused with the group bot.
    try:
        info = requests.get("https://api.line.me/v2/bot/info", headers=headers, timeout=8)
        info.raise_for_status()
        if info.json().get("basicId") != "@129ntjqs":
            raise ValueError("wrong official account")
    except (requests.RequestException, ValueError, TypeError):
        return _page("官方 LINE 設定待確認", "沒有傳送訊息，請先確認客戶官方帳號的設定。", 502)
    receipt = {"id": "TEST-" + uuid.uuid4().hex[:12].upper(),
               "status": "已建立測試紀錄，通知處理中；請勿重複送出。"}
    # Atomic, owner-wide reservation: simultaneous clicks and fresh logins cannot
    # send a second receipt. Retain failed/ambiguous requests for 24 hours too.
    try:
        _put(_receipt_key(), "receipt", receipt, 86400)
    except sqlite3.IntegrityError:
        return _owner_page(profile, token)
    except ValueError:
        return _page("請稍後再試", "尚未傳送訊息。", 429)
    text = ("【測試訂單，請勿出貨】\n我們收到你的測試訂單了。\n"
            + "編號：" + receipt["id"] + "\n5斤百香果 × 1箱\n商品金額：NT$650\n"
            + "僅測試 LINE 登入與通知，不收款、不配送；未寫入正式訂單。")
    try:
        result = requests.post("https://api.line.me/v2/bot/message/push", headers={
            **headers, "X-Line-Retry-Key": str(uuid.uuid4())}, json={
                "to": os.environ["LINE_LOGIN_TEST_OWNER_ID"],
                "messages": [{"type": "text", "text": text}]}, timeout=8)
        result.raise_for_status()
        receipt["status"] = "LINE 已接受通知請求。請打開手機確認是否收到；這不代表正式訂單成立。"
    except requests.RequestException:
        receipt["status"] = "通知結果未確認，請先查看手機 LINE。為避免重複通知，不會自動重送。"
    with closing(_db()) as db, db:
        db.execute("UPDATE trial SET payload=? WHERE key=? AND kind='receipt'",
                   (json.dumps(receipt), _hash(_receipt_key())))
    return redirect(PATH, 303)


@line_login_test.post("/start")
def start():
    if not _ready() or not _csrf_ok():
        return _page("無法開始登入", "請回到測試頁重新開始。", 403)
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    try:
        _put(state, "flow", {"browser": _hash(request.cookies[COOKIE]), "nonce": nonce, "verifier": verifier}, 300)
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
        profile = {"name": claims.get("name", "LINE 使用者"), "picture": picture, "tail": claims["sub"][-4:], "friend": friend}
        token = secrets.token_urlsafe(32)
        _put(token, "session", profile, 900)
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
