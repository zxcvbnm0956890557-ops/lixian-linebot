from __future__ import annotations

import json
import os
import re

from openai import OpenAI

from heuristics import extract_signals
from models import OrderBatchExtraction


SYSTEM_PROMPT = """
你是「李鮮百香果」訂單資料抽取器。你的工作不是聊天，而是忠實抽取配送資料。

重要規則：
1. 絕對不能依照文字行的順序決定姓名、電話、地址。
2. 不可自行臆測缺少的姓名、電話、地址或數量。
3. 訂購人、實際收件人、黑貓寄件人姓名是三個不同欄位。
4. 一般訂單 sender_mode=farm、sender_name 留空。
5. 客人說「送朋友／送老闆／寄件人寫我／用我的名字寄」時，只有黑貓寄件人姓名改成客人姓名；
   寄件電話與地址仍由後端固定使用李鮮資料。此時 sender_mode=customer。
6. 客人明確指定「寄件人寫王小明」時，sender_mode=custom、sender_name=王小明。
7. 「5斤32」、「5斤32箱」、「5斤：32」都代表 five_jin_boxes=32；10斤同理。
   中文寫法也相同，例如「五斤四箱」代表 five_jin_boxes=4，「十斤兩箱」代表 ten_jin_boxes=2。
8. 農民收據、公司抬頭、統編放 receipt_note，不可放進姓名或地址。
9. 「管理員代收」、「回國再配送」等放 note。
10. 同一段文字可能包含多筆訂單，每一位收件人必須各自建立一個 orders 項目，禁止把兩位收件人的資料合併。
11. 新的規格通常代表下一筆訂單開始。例如「09372369135斤4箱」應切成電話 0937236913 與下一筆規格 5斤4箱。
12. 姓名、電話、地址與規格必須能在原文中互相配對；不能確定訂單邊界時放入 batch_ambiguities，不可猜測。
13. 每筆訂單的配送／出貨日期放在該筆 note，例如「27號配送」、「7/29出貨」。
14. 電話可以是 09 開頭手機，也可以是含區碼的臺灣市話，例如 04-7510163。
15. confidence 只有在該筆姓名、電話、完整地址、箱數與寄件人規則都清楚時才可高於 0.8。
16. 不要把電話當姓名、不要把地址當電話、不要把備註當姓名。
17. 「五斤四箱」等商品規格絕對不是人名；「27號配送」、「7/29出貨」等日期也不是地址或電話。
18. 即使原文有「姓名：」「電話：」「地址：」等標籤，也必須依內容型態驗證，不可只相信標籤。
""".strip()


PHONE_THEN_ORDER_RE = re.compile(
    r"(?<!\d)((?:09\d{8})|(?:0[2-8]\d{7,8}))(?=(?:5|10|五|十)\s*斤)"
)


def normalize_order_boundaries(raw_text: str) -> str:
    """只切開可確定的「完整電話＋下一筆規格」，不改動其他內容。"""

    compact = re.sub(r"(?<=\d)[ -](?=\d)", "", raw_text)
    return PHONE_THEN_ORDER_RE.sub(r"\1\n", compact)


class OrderParser:
    def __init__(self, client: OpenAI | None = None, model: str | None = None):
        self.client = client or OpenAI()
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

    def parse(self, raw_text: str, line_display_name: str = "") -> OrderBatchExtraction:
        normalized_text = normalize_order_boundaries(raw_text)
        signals = extract_signals(normalized_text)
        context = {
            "line_display_name": line_display_name or None,
            "deterministic_phone_candidates": signals.phones,
            "deterministic_address_candidates": signals.address_lines,
            "deterministic_name_candidates": signals.name_candidates,
            "deterministic_five_jin_boxes": signals.five_jin_boxes,
            "deterministic_ten_jin_boxes": signals.ten_jin_boxes,
            "raw_messages": normalized_text,
            "original_raw_messages": raw_text,
        }
        response = self.client.responses.parse(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=json.dumps(context, ensure_ascii=False),
            text_format=OrderBatchExtraction,
            timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60")),
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("OpenAI 沒有回傳可驗證的訂單資料")

        # 只有整批確定只有一筆，而且原文恰好一個姓名時，才允許補唯一姓名。
        if len(parsed.orders) == 1:
            order = parsed.orders[0]
            if (
                not order.recipient_name
                and len(signals.name_candidates) == 1
                and not order.ambiguities
                and not parsed.batch_ambiguities
                and order.sender_mode == "farm"
            ):
                order.recipient_name = signals.name_candidates[0]
            if not order.customer_name and order.recipient_name:
                order.customer_name = order.recipient_name
        return parsed
