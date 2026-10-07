"""Private website-to-LINE relay; never forwards customer contact details."""
from __future__ import annotations

import hmac
import os
import re

from flask import Blueprint, jsonify, request

from line_api import push_text

website_notifications = Blueprint("website_notifications", __name__)
BACKEND_URL = "https://docs.google.com/spreadsheets/d/1Of-XP5ycz0JjsNdnxgaNIQ34oJGACxjhLsbYyenGET0/edit#gid=1930047993"


@website_notifications.post("/website/order-notification")
def notify_website_order():
    secret = os.getenv("WEBSITE_NOTIFICATION_SECRET", "")
    target = os.getenv("WEBSITE_NOTIFICATION_TARGET_ID", "")
    if len(secret) < 32 or not re.fullmatch(r"C[0-9a-f]{32}", target):
        return jsonify({"accepted": False}), 503
    supplied = request.headers.get("Authorization", "")
    if not hmac.compare_digest(supplied.encode(), ("Bearer " + secret).encode()):
        return jsonify({"accepted": False}), 401
    if request.content_length is None or request.content_length > 2048:
        return jsonify({"accepted": False}), 413
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - {"reference", "fiveQty", "tenQty", "test"}:
        return jsonify({"accepted": False}), 400
    reference = data.get("reference", "")
    five, ten = data.get("fiveQty"), data.get("tenQty")
    if (not isinstance(reference, str) or not re.fullmatch(r"[a-zA-Z0-9-]{8,64}", reference)
            or type(five) is not int or type(ten) is not int
            or not 0 <= five <= 10000 or not 0 <= ten <= 10000 or five + ten == 0
            or type(data.get("test", False)) is not bool):
        return jsonify({"accepted": False}), 400
    heading = "🧪 網站通知測試，請勿出貨" if data.get("test") else "📦 網站收到新訂單"
    text = f"{heading}\n5斤：{five}箱\n10斤：{ten}箱\n通知識別：{reference}\n請到後台核對訂單與出貨資料：\n{BACKEND_URL}"
    try:
        push_text(target, text)
    except Exception:
        # No request body or credentials in logs/responses. The website must
        # keep an already accepted Google order successful if this relay fails.
        return jsonify({"accepted": False}), 502
    return jsonify({"accepted": True}), 200
