from __future__ import annotations

import os
import re

from heuristics import normalize_phone_candidate
from models import CleanOrder, OrderExtraction, ValidationResult


ADDRESS_TOKENS = ("市", "縣", "區", "鄉", "鎮", "村", "里", "路", "街", "巷", "弄", "號")
NAME_FORBIDDEN = ("市", "縣", "區", "鄉", "鎮", "路", "街", "巷", "弄", "號", "斤", "箱")


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _valid_name(value: str) -> bool:
    if not 2 <= len(value) <= 30:
        return False
    if any(char.isdigit() for char in value):
        return False
    return not any(token in value for token in NAME_FORBIDDEN)


def _valid_address(value: str) -> bool:
    if len(value) < 8:
        return False
    has_city = "市" in value or "縣" in value
    score = sum(token in value for token in ADDRESS_TOKENS)
    return has_city and score >= 3


def validate_extraction(extraction: OrderExtraction) -> ValidationResult:
    issues: list[str] = list(extraction.ambiguities)

    customer_name = _clean_text(extraction.customer_name)
    recipient_name = _clean_text(extraction.recipient_name)
    phone = normalize_phone_candidate(extraction.recipient_phone or "")
    address = _clean_text(extraction.recipient_address).replace("台北市", "臺北市")

    if not _valid_name(recipient_name):
        issues.append("收件人姓名缺少或格式不合理")
    if not re.fullmatch(r"09\d{8}", phone):
        issues.append("收件人手機必須是 09 開頭共 10 碼")
    if not _valid_address(address):
        issues.append("收件地址缺少縣市或路街巷號等必要資訊")
    if extraction.five_jin_boxes + extraction.ten_jin_boxes <= 0:
        issues.append("沒有辨識到 5斤或10斤箱數")

    farm_name = os.getenv("FARM_SENDER_NAME", "李鮮")
    sender_name = _clean_text(extraction.sender_name)
    if extraction.sender_mode == "farm":
        sender_name = farm_name
    elif extraction.sender_mode == "customer":
        sender_name = sender_name or customer_name
    if not _valid_name(sender_name):
        issues.append("送禮寄件人姓名不明確")

    if not customer_name:
        customer_name = sender_name if extraction.sender_mode != "farm" else recipient_name

    if extraction.confidence < 0.80:
        issues.append("AI 判讀信心不足，需人工確認")

    unique_issues = tuple(dict.fromkeys(issue for issue in issues if issue))
    if unique_issues:
        return ValidationResult(order=None, issues=unique_issues)

    return ValidationResult(
        order=CleanOrder(
            customer_name=customer_name,
            recipient_name=recipient_name,
            recipient_phone=phone,
            recipient_address=address,
            five_jin_boxes=extraction.five_jin_boxes,
            ten_jin_boxes=extraction.ten_jin_boxes,
            sender_name=sender_name,
            sender_phone=os.getenv("FARM_SENDER_PHONE", "0986184111"),
            sender_address=os.getenv(
                "FARM_SENDER_ADDRESS", "南投縣國姓鄉大石村中正路三段224-10號"
            ),
            note=_clean_text(extraction.note),
            receipt_note=_clean_text(extraction.receipt_note),
            confidence=extraction.confidence,
        ),
        issues=(),
    )

