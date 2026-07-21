from datetime import datetime, timezone

from database import BotDatabase


def event(event_id, message_id, body, timestamp_ms, key="group:user"):
    return {
        "event_id": event_id,
        "conversation_key": key,
        "destination_id": "group",
        "user_id": "user",
        "message_id": message_id,
        "body": body,
        "timestamp_ms": timestamp_ms,
    }


def test_duplicate_line_webhook_is_ignored(tmp_path):
    database = BotDatabase(str(tmp_path / "bot.db"))
    item = event("evt-1", "msg-1", "10斤1", 1)
    assert database.enqueue_event(item) is True
    assert database.enqueue_event(item) is False


def test_separate_messages_are_combined_in_order(tmp_path):
    database = BotDatabase(str(tmp_path / "bot.db"))
    database.enqueue_event(event("evt-1", "msg-1", "江珈儀", 1000))
    database.enqueue_event(event("evt-2", "msg-2", "0978006578", 2000))
    database.enqueue_event(event("evt-3", "msg-3", "雲林縣斗六市崙南路128號", 3000))
    assert database.ingest_queued_events() == 3
    with database.connect() as connection:
        rows = connection.execute("SELECT * FROM conversations").fetchall()
    assert len(rows) == 1
    assert rows[0]["raw_text"] == "江珈儀\n0978006578\n雲林縣斗六市崙南路128號"


def test_completed_customer_can_start_another_order(tmp_path):
    database = BotDatabase(str(tmp_path / "bot.db"))
    database.enqueue_event(event("evt-1", "msg-1", "第一筆", 1000))
    database.ingest_queued_events()
    with database.connect() as connection:
        conversation_id = connection.execute("SELECT id FROM conversations").fetchone()[0]
        connection.execute(
            "UPDATE conversations SET status='completed' WHERE id=?", (conversation_id,)
        )
    database.enqueue_event(event("evt-2", "msg-2", "第二筆", 2000))
    database.ingest_queued_events()
    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    assert count == 2
