import itertools

import pytest

from heuristics import extract_signals
from order_parser import normalize_order_boundaries


NAME = "王素卿"
ADDRESS = "彰化市自強南路451巷7號10樓"

FIELD_ORDERS = list(itertools.permutations(("quantity", "name", "phone", "address")))
SEPARATORS = ("\n", "，", "、", "；", " ")
MOBILE_STYLES = ("0937236913", "0937-236-913", "0937 236 913", "+886 937236913")
MOBILE_QUANTITY_STYLES = (
    ("5斤4箱", 4),
    ("5斤+4", 4),
    ("5斤：4", 4),
    ("五斤四箱", 4),
    ("五斤兩箱", 2),
)
LABELED_FIELDS = (
    {
        "quantity": "規格：5斤4箱",
        "name": f"姓名：{NAME}",
        "phone": "電話：0937236913",
        "address": f"地址：{ADDRESS}",
    },
    {
        "quantity": "訂購5斤+4",
        "name": f"收件人姓名:{NAME}",
        "phone": "手機:0937-236-913",
        "address": f"收貨住址:{ADDRESS}",
    },
    {
        "quantity": "五斤四箱",
        "name": f"訂購人資訊｜姓名：{NAME}",
        "phone": "聯絡電話：+886 937236913",
        "address": f"配送地址：{ADDRESS}",
    },
)


def _render(fields, separator, phone, quantity):
    values = {
        "quantity": quantity,
        "name": NAME,
        "phone": phone,
        "address": ADDRESS,
    }
    return separator.join(values[field] for field in fields)


FORMAT_CASES = [
    (fields, separator, phone, quantity, expected)
    for fields in FIELD_ORDERS
    for separator in SEPARATORS
    for phone in MOBILE_STYLES
    for quantity, expected in MOBILE_QUANTITY_STYLES
]


@pytest.mark.parametrize(
    ("fields", "separator", "phone", "quantity", "expected"),
    FORMAT_CASES,
)
def test_2400_common_order_format_combinations(
    fields, separator, phone, quantity, expected
):
    text = _render(fields, separator, phone, quantity)
    signals = extract_signals(text)
    assert signals.phones == ("0937236913",)
    assert signals.five_jin_boxes == expected
    assert signals.ten_jin_boxes == 0
    assert signals.address_lines


@pytest.mark.parametrize(
    ("fields", "separator", "values"),
    [
        (fields, separator, values)
        for fields in FIELD_ORDERS
        for separator in ("\n", "，", "；", " ")
        for values in LABELED_FIELDS
    ],
)
def test_288_labeled_field_combinations(fields, separator, values):
    text = separator.join(values[field] for field in fields)
    signals = extract_signals(text)
    assert signals.phones == ("0937236913",)
    assert signals.name_candidates == (NAME,)
    assert signals.five_jin_boxes == 4
    assert signals.address_lines


@pytest.mark.parametrize(
    ("phone", "normalized"),
    [
        ("04-7510163", "047510163"),
        ("04 7510163", "047510163"),
        ("047510163", "047510163"),
        ("+886 4 7510163", "047510163"),
        ("02-2345-6789", "0223456789"),
        ("+886 2 2345 6789", "0223456789"),
    ],
)
def test_landline_writing_styles(phone, normalized):
    signals = extract_signals(f"姓名：{NAME}\n電話：{phone}\n收貨住址：{ADDRESS}\n5斤2")
    assert signals.phones == (normalized,)
    assert signals.name_candidates == (NAME,)
    assert signals.five_jin_boxes == 2


@pytest.mark.parametrize(
    ("text", "five", "ten"),
    [
        ("5斤1箱", 1, 0),
        ("5斤2箱", 2, 0),
        ("5斤4箱", 4, 0),
        ("10斤1箱", 0, 1),
        ("10斤2箱", 0, 2),
        ("5斤1箱＋10斤1箱", 1, 1),
        ("5斤2箱、10斤1箱", 2, 1),
        ("五斤二箱，十斤一箱", 2, 1),
        ("5斤20", 20, 0),
        ("五斤廿箱", 20, 0),
        ("五斤三十二箱", 32, 0),
    ],
)
def test_product_quantity_writing_styles(text, five, ten):
    signals = extract_signals(text)
    assert (signals.five_jin_boxes, signals.ten_jin_boxes) == (five, ten)


@pytest.mark.parametrize(
    "suffix",
    ("5斤4", "5斤4箱", "五斤四箱", "10斤1", "十斤一箱"),
)
def test_phone_stuck_to_next_order_is_split(suffix):
    raw = f"王秀貴\n彰化市中山路二段521號4樓\n0937236913{suffix}\n花如彬0905276555"
    normalized = normalize_order_boundaries(raw)
    assert f"0937236913\n{suffix}" in normalized


@pytest.mark.parametrize(
    "text",
    [
        "王小明\n彰化市中山路二段521號4樓",  # 缺電話與規格
        "5斤4\n0937236913",  # 缺姓名與地址
        "姓名：王小明\n電話：不知道\n地址：稍後補\n5斤4",  # 明確無效電話地址
        "王小明 0937236913 彰化市中山路二段521號4樓",  # 缺規格
    ],
)
def test_incomplete_orders_do_not_fabricate_missing_hard_signals(text):
    signals = extract_signals(text)
    assert (
        not signals.phones
        or not signals.address_lines
        or signals.five_jin_boxes + signals.ten_jin_boxes == 0
    )
