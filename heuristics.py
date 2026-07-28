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
    "斤", "箱", "規格", "訂購", "資訊", "聯絡", "手機",
    "到貨", "出貨", "上午", "中午", "下午", "晚上", "前到",
)


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _looks_like_name(value: str) -> bool:
    return bool(
        NAME_TOKEN_RE.fullmatch(value)
        and not any(word in value for word in NON_NAME_WORDS)
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
    # 客人常把地址、電話、姓名寫在同一行；先用常見標點切成欄位，
    # 讓地址候選不會連同電話與姓名一起交給驗證器。
    for raw_line in re.split(r"[\n,，、;；|｜]+", text):
        line = re.sub(
            r"^(?:地址|住址|收貨住址|收件地址|寄送地址|配送地址)\s*[：:]\s*",
            "",
            raw_line.strip(" ,，。"),
        )
        score = sum(word in line for word in ADDRESS_WORDS)
        if score >= 3 and any(city in line for city in ("市", "縣")):
            _append_unique(address_lines, line)

    name_candidates = []
    for match in LABELED_NAME_RE.finditer(text):
        candidate = match.group(1)
        if _looks_like_name(candidate):
            _append_unique(name_candidates, candidate)
    for part in re.split(r"[\n,，、;；]", text):
        candidate = part.strip(" ：:。,.， ")
        if _looks_like_name(candidate):
            _append_unique(name_candidates, candidate)

        # 支援「花如彬0905276555」及「0905276555花如彬」。只取緊貼電話的
        # 2-6 字中文，避免依照欄位順序猜姓名。
        compact = re.sub(r"\s+", "", candidate)
        attached_patterns = (
            r"([\u3400-\u9fff·]{2,6})(?=(?:\+?886)?0?9\d{8}|0[2-8]\d{7,8})",
            r"(?:(?:\+?886)?0?9\d{8}|0[2-8]\d{7,8})([\u3400-\u9fff·]{2,6})",
        )
        for pattern in attached_patterns:
            for match in re.finditer(pattern, compact):
                attached_name = match.group(1)
                if _looks_like_name(attached_name):
                    _append_unique(name_candidates, attached_name)

    return OrderSignals(
        phones=tuple(phones),
        address_lines=tuple(address_lines),
        name_candidates=tuple(name_candidates),
        five_jin_boxes=qty5,
        ten_jin_boxes=qty10,
    )
