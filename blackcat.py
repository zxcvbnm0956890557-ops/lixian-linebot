from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from models import CleanOrder


BLACKCAT_HEADERS = [
    "收件人姓名", "收件人電話", "收件人手機", "收件人地址", "代收金額或到付", "件數",
    "品名(詳參數表)", "備註", "訂單編號", "希望配達時間(詳參數表)",
    "出貨日期(YYYY/MM/DD)", "預定配達日期(YYYY/MM/DD)", "溫層(詳參數表)",
    "尺寸(詳參數表)", "寄件人姓名", "寄件人電話", "寄件人手機", "寄件人地址",
    "保值金額(20001~10萬之間)-會產生額外費用", "品名說明", "是否列印(Y/N)",
    "是否捐贈(Y/N)", "統一編號", "手機載具", "愛心碼", "可刷卡(Y/N)", "手機支付(Y/N)",
]


@dataclass(frozen=True)
class PackageSpec:
    five: int
    ten: int
    label: str
    shipping_cost: int


SPECS = (
    PackageSpec(2, 1, "5斤+2、10斤+1", 200),
    PackageSpec(1, 1, "5斤+1、10斤+1", 200),
    PackageSpec(4, 0, "5斤+4", 200),
    PackageSpec(0, 2, "10斤+2", 200),
    PackageSpec(2, 0, "5斤+2", 180),
    PackageSpec(1, 0, "5斤+1", 150),
    PackageSpec(0, 1, "10斤+1", 150),
)


def split_packages(five: int, ten: int) -> list[PackageSpec]:
    memo: dict[tuple[int, int], tuple[int, int, list[PackageSpec]] | None] = {}

    def solve(r5: int, r10: int):
        if (r5, r10) == (0, 0):
            return (0, 0, [])
        if (r5, r10) in memo:
            return memo[(r5, r10)]
        best = None
        for spec in SPECS:
            if spec.five <= r5 and spec.ten <= r10:
                rest = solve(r5 - spec.five, r10 - spec.ten)
                if rest is None:
                    continue
                candidate = (1 + rest[0], spec.shipping_cost + rest[1], [spec, *rest[2]])
                if best is None or candidate[:2] < best[:2]:
                    best = candidate
        memo[(r5, r10)] = best
        return best

    result = solve(five, ten)
    if result is None:
        raise ValueError(f"無法拆分規格：5斤{five}箱、10斤{ten}箱")
    return result[2]


def build_blackcat_rows(order: CleanOrder, order_id: str) -> list[list[str]]:
    packages = split_packages(order.five_jin_boxes, order.ten_jin_boxes)
    rows: list[list[str]] = []
    for index, package in enumerate(packages, start=1):
        note_parts = [package.label]
        if len(packages) > 1:
            note_parts.append(f"第{index}/{len(packages)}張")
        if order.note:
            note_parts.append(order.note)
        rows.append([
            order.recipient_name, "", order.recipient_phone, order.recipient_address, "", "1", "4",
            " ".join(note_parts), f"{order_id}-{index:02d}", "4", "", "", "1", "1",
            order.sender_name, "", order.sender_phone, order.sender_address, "", "百香果", "", "", "", "", "", "N", "N",
        ])
    return rows


def export_blackcat_csv(rows: list[list[str]], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(BLACKCAT_HEADERS)
        writer.writerows(rows)
    return output

