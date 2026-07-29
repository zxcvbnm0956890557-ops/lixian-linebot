from database import BotDatabase
from models import OrderBatchExtraction, OrderExtraction
from worker import OrderWorker, split_test_reply_prefix


def test_test_prefix_enables_reply_and_is_removed_before_parsing():
    enabled, text = split_test_reply_prefix("#測試\n5斤1\n王小明")
    assert enabled is True
    assert text == "5斤1\n王小明"


def test_normal_order_remains_silent():
    enabled, text = split_test_reply_prefix("5斤1\n王小明")
    assert enabled is False
    assert text == "5斤1\n王小明"


class FakeBatchParser:
    def parse(self, _raw_text):
        return OrderBatchExtraction(
            orders=[
                OrderExtraction(
                    recipient_name="王秀貴",
                    recipient_phone="0937236913",
                    recipient_address="彰化市中山路二段521號4樓",
                    five_jin_boxes=20,
                    note="",
                    confidence=0.98,
                ),
                OrderExtraction(
                    recipient_name="花如彬",
                    recipient_phone="0905276555",
                    recipient_address="新北市汐止區大同路一段337巷16弄42號",
                    five_jin_boxes=4,
                    note="27號配送",
                    confidence=0.98,
                ),
                OrderExtraction(
                    recipient_name="王素卿",
                    recipient_phone="04-7510163",
                    recipient_address="彰化市自強南路451巷7號10樓",
                    five_jin_boxes=2,
                    note="7/29出貨",
                    confidence=0.98,
                ),
            ]
        )


def test_one_message_can_save_three_separate_orders(tmp_path):
    database = BotDatabase(str(tmp_path / "bot.db"))
    database.enqueue_event(
        {
            "event_id": "evt-batch",
            "conversation_key": "group:user",
            "destination_id": "group",
            "user_id": "user",
            "message_id": "msg-batch",
            "body": "#測試\n多筆不同格式",
            "timestamp_ms": 1,
        }
    )
    replies = []
    worker = OrderWorker(
        database,
        parser=FakeBatchParser(),
        notifier=lambda destination, text: replies.append((destination, text)),
    )
    worker.quiet_seconds = 0
    worker.run_once()

    with database.connect() as connection:
        orders = connection.execute(
            "SELECT source_index, recipient_name, recipient_phone FROM orders ORDER BY source_index"
        ).fetchall()
    assert [(row["source_index"], row["recipient_name"]) for row in orders] == [
        (1, "王秀貴"),
        (2, "花如彬"),
        (3, "王素卿"),
    ]
    assert orders[2]["recipient_phone"] == "047510163"
    assert len(replies) == 1
    assert "已辨識 3 筆訂單" in replies[0][1]
def test_report_group_command_is_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    database = BotDatabase(str(tmp_path / "bot.db"))
    notices = []
    worker = OrderWorker(
        database,
        parser=object(),
        notifier=lambda destination, text: notices.append((destination, text)),
    )
    conversation = {
        "id": 1,
        "raw_text": "#設定報表群組",
        "destination_id": "group-test-123",
    }
    with database.connect() as db:
        db.execute(
            """INSERT INTO conversations
            (id, conversation_key, destination_id, user_id, raw_text, last_message_at, status)
            VALUES (1, 'group:user', 'group-test-123', 'user', '#設定報表群組',
                    '2026-07-29T00:00:00+00:00', 'processing')"""
        )

    worker._process(conversation)

    assert database.get_setting("line_report_target_id") == "group-test-123"
    assert notices == [
        ("group-test-123", "✅ 這個群組已設定為晚上9點測試報表群組。")
    ]
