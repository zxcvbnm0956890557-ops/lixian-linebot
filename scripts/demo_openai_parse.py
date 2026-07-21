from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env.local")

from order_parser import OrderParser  # noqa: E402
from validation import validate_extraction  # noqa: E402


SAMPLES = [
    "10斤1\n江珈儀\n0978006578\n雲林縣斗六市崙南路128號",
    "5斤20\n台北市中山區松江路410號17F，0979869999，李明勳\n管理員代收",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="用真實 OpenAI API 測試訂單辨識")
    parser.add_argument("text", nargs="?", help="要測試的訂單文字；未填則跑內建兩筆")
    arguments = parser.parse_args()
    for index, raw_text in enumerate([arguments.text] if arguments.text else SAMPLES, start=1):
        extraction = OrderParser().parse(raw_text)
        validation = validate_extraction(extraction)
        print(f"\n測試 {index}")
        print(extraction.model_dump_json(indent=2))
        print("結果：", "可接受" if validation.order else "待人工確認")
        if validation.issues:
            print("原因：", "；".join(validation.issues))


if __name__ == "__main__":
    main()
