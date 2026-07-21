from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackcat import build_blackcat_rows, export_blackcat_csv  # noqa: E402
from models import CleanOrder  # noqa: E402


def main() -> None:
    order = CleanOrder(
        customer_name="李明勳",
        recipient_name="李明勳",
        recipient_phone="0979869999",
        recipient_address="臺北市中山區松江路410號17F",
        five_jin_boxes=20,
        ten_jin_boxes=0,
        sender_name="李鮮",
        sender_phone="0986184111",
        sender_address="南投縣國姓鄉中正路三段224-10號",
        note="管理員代收",
        receipt_note="",
        confidence=0.99,
    )
    output = export_blackcat_csv(
        build_blackcat_rows(order, "CODEX-TEST-001"),
        ROOT / "exports" / "黑貓匯入_測試.csv",
    )
    print(output)


if __name__ == "__main__":
    main()
