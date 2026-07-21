from __future__ import annotations

import base64
import hashlib
import hmac
import os

import requests


def verify_line_signature(raw_body: bytes, signature: str) -> bool:
    secret = os.getenv("LINE_CHANNEL_SECRET", "")
    if not secret or not signature:
        return False
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, signature)


def push_text(destination_id: str, text: str) -> None:
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("尚未設定 LINE_CHANNEL_ACCESS_TOKEN")
    response = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": destination_id, "messages": [{"type": "text", "text": text[:5000]}]},
        timeout=15,
    )
    response.raise_for_status()

