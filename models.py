from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


class OrderExtraction(BaseModel):
    """AI 僅負責抽取，不代表資料已可出貨。"""

    customer_name: str | None = Field(
        default=None, description="在 LINE 下單的客人姓名；不知道就留空"
    )
    recipient_name: str | None = Field(
        default=None, description="實際收件人的姓名"
    )
    recipient_phone: str | None = Field(
        default=None, description="實際收件人的電話"
    )
    recipient_address: str | None = Field(
        default=None, description="實際收件人的完整地址"
    )
    five_jin_boxes: int = Field(default=0, ge=0, le=999)
    ten_jin_boxes: int = Field(default=0, ge=0, le=999)
    sender_mode: Literal["farm", "customer", "custom"] = "farm"
    sender_name: str | None = Field(
        default=None,
        description="黑貓寄件人姓名；一般訂單留空，送禮才填客人指定姓名",
    )
    note: str | None = None
    receipt_note: str | None = Field(
        default=None, description="農民收據、公司抬頭、統編等非配送資訊"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    ambiguities: list[str] = Field(default_factory=list)


class OrderBatchExtraction(BaseModel):
    """一段 LINE 文字可能同時包含多位收件人的訂單。"""

    orders: list[OrderExtraction] = Field(default_factory=list, max_length=50)
    batch_ambiguities: list[str] = Field(
        default_factory=list,
        description="無法確定訂單邊界或欄位歸屬時的整批問題",
    )


@dataclass(frozen=True)
class CleanOrder:
    customer_name: str
    recipient_name: str
    recipient_phone: str
    recipient_address: str
    five_jin_boxes: int
    ten_jin_boxes: int
    sender_name: str
    sender_phone: str
    sender_address: str
    note: str
    receipt_note: str
    confidence: float


@dataclass(frozen=True)
class ValidationResult:
    order: CleanOrder | None
    issues: tuple[str, ...]
