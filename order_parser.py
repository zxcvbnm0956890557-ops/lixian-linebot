from __future__ import annotations

import json
import os

from openai import OpenAI

from heuristics import extract_signals
from models import OrderExtraction


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
8. 農民收據、公司抬頭、統編放 receipt_note，不可放進姓名或地址。
9. 「管理員代收」、「回國再配送」等放 note。
10. 若同一段文字含兩個可能的姓名、電話或地址而無法確定對應關係，列入 ambiguities 並降低 confidence。
11. confidence 只有在姓名、手機、完整地址、箱數與寄件人規則都清楚時才可高於 0.8。
12. 不要把電話當姓名、不要把地址當電話、不要把備註當姓名。
""".strip()


class OrderParser:
    def __init__(self, client: OpenAI | None = None, model: str | None = None):
        self.client = client or OpenAI()
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

    def parse(self, raw_text: str, line_display_name: str = "") -> OrderExtraction:
        signals = extract_signals(raw_text)
        context = {
            "line_display_name": line_display_name or None,
            "deterministic_phone_candidates": signals.phones,
            "deterministic_address_candidates": signals.address_lines,
            "deterministic_name_candidates": signals.name_candidates,
            "deterministic_five_jin_boxes": signals.five_jin_boxes,
            "deterministic_ten_jin_boxes": signals.ten_jin_boxes,
            "raw_messages": raw_text,
        }
        response = self.client.responses.parse(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=json.dumps(context, ensure_ascii=False),
            text_format=OrderExtraction,
            timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60")),
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("OpenAI 沒有回傳可驗證的訂單資料")

        # AI 偶爾會把唯一姓名填到訂購人、卻漏掉收件人。只有原文能嚴格切出
        # 恰好一個姓名、而且 AI 沒回報歧義時才補值；兩個姓名或送禮情境不猜。
        if (
            not parsed.recipient_name
            and len(signals.name_candidates) == 1
            and not parsed.ambiguities
            and parsed.sender_mode == "farm"
        ):
            parsed.recipient_name = signals.name_candidates[0]
        if not parsed.customer_name and parsed.recipient_name:
            parsed.customer_name = parsed.recipient_name
        return parsed
