from __future__ import annotations

import re
from dataclasses import dataclass


PHONE_RE = re.compile(r"(?<!\d)(?:\+?886[-\s]?)?0?9\d(?:[-\s]?\d){7}(?!\d)")
QTY_RE = re.compile(r"(?<!\d)(10|5)\s*斤\s*(?:[：:]|[+＋xX×*])?\s*(\d{1,3})\s*(?:箱)?")
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
NON_NAME_WORDS = (
    "代收", "備註", "收據", "統編", "公司", "抬頭", "配送", "寄件", "收件",
    "送禮", "送朋友", "送老闆", "回國", "電話", "地址", "姓名", "管理員",
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
        if weight == "5":
            qty5 += int(amount)
        else:
            qty10 += int(amount)

    address_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip(" ,，。")
        score = sum(word in line for word in ADDRESS_WORDS)
        if score >= 3 and any(city in line for city in ("市", "縣")):
            address_lines.append(line)

    name_candidates = []
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
