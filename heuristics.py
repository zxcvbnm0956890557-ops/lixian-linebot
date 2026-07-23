from __future__ import annotations

import re
from dataclasses import dataclass


PHONE_RE = re.compile(
    r"(?<!\d)(?:"
    r"(?:\+?886[-\s]?)?0?9\d(?:[-\s]?\d){7}"
    r"|0[2-8](?:[-\s]?\d){7,8}"
    r"|\+?886[-\s]?0?[2-8](?:[-\s]?\d){7,8}"
    r")(?!\d)"
)
QTY_RE = re.compile(
    r"(?<!\d)(10|5|十|五)\s*斤\s*(?:[：:]|[+＋xX×*])?\s*"
    r"(\d{1,3}|[一二兩三四五六七八九十廿卅]{1,3})\s*(?:箱)?"
)
ADDRESS_WORDS = (
    "市",
    "縣",
    "區",
    "鄉",
    "鎮",
    "村",
    "里",
    "路",
    "街",
    "巷",
    "弄",
    "號",
)
NAME_TOKEN_RE = re.compile(r"^[\u3400-\u9fff·]{2,6}$")
LABELED_NAME_RE = re.compile(
    r"(?:收件人(?:姓名)?|訂購人(?:姓名)?|姓名)\s*[：:]\s*([\u3400-\u9fff·]{2,6})"
)
NON_NAME_WORDS = (
    "代收", "備註", "收據", "統編", "公司", "抬頭", "配送", "寄件", "收件",
    "送禮", "送朋友", "送老闆", "回國", "電話", "地址", "姓名", "管理員",
    "斤", "箱",
)


@dataclass(frozen=True)
class OrderSignals:
    phones: tuple[str, ...]
    address_lines: tuple[str, ...]
    name_candidates: tuple[str, ...]
    five_jin_boxes: int
    ten_jin_boxes: int


def normalize_phone_candidate(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if digits.startswith("886"):
        digits = "0" + digits[3:]
    if len(digits) == 9 and digits.startswith("9"):
        digits = "0" + digits
    return digits


def parse_quantity_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    normalized = value.replace("兩", "二")
    if normalized == "廿":
        return 20
    if normalized == "卅":
        return 30
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if normalized == "十":
        return 10
    if "十" in normalized:
        tens_text, ones_text = normalized.split("十", 1)
        tens = digits.get(tens_text, 1) if tens_text else 1
        ones = digits.get(ones_text, 0) if ones_text else 0
        return tens * 10 + ones
    if len(normalized) == 1 and normalized in digits:
        return digits[normalized]
    raise ValueError(f"不支援的中文數量：{value}")


def extract_signals(text: str) -> OrderSignals:
    """與文字順序無關，只提供硬訊號給 AI 與驗證器。"""

    phones = []
    for match in PHONE_RE.finditer(text):
        normalized = normalize_phone_candidate(match.group(0))
        if normalized not in phones:
            phones.append(normalized)

    qty5 = 0
    qty10 = 0
    for weight, amount in QTY_RE.findall(text):
        parsed_amount = parse_quantity_number(amount)
        if weight in ("5", "五"):
            qty5 += parsed_amount
        else:
            qty10 += parsed_amount

    address_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip(" ,，。")
        score = sum(word in line for word in ADDRESS_WORDS)
        if score >= 3 and any(city in line for city in ("市", "縣")):
            address_lines.append(line)

    name_candidates = []
    for match in LABELED_NAME_RE.finditer(text):
        candidate = match.group(1)
        if candidate not in name_candidates:
            name_candidates.append(candidate)
    for part in re.split(r"[\n,，、;；]", text):
        candidate = part.strip(" ：:。,.， ")
        if not NAME_TOKEN_RE.fullmatch(candidate):
            continue
        if any(word in candidate for word in NON_NAME_WORDS):
            continue
        if candidate not in name_candidates:
            name_candidates.append(candidate)

    return OrderSignals(
        phones=tuple(phones),
        address_lines=tuple(address_lines),
        name_candidates=tuple(name_candidates),
        five_jin_boxes=qty5,
        ten_jin_boxes=qty10,
    )
