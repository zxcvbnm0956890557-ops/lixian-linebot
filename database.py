from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from models import CleanOrder, OrderBatchExtraction, OrderExtraction


UTC = timezone.utc


class BotDatabase:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._init_lock, self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    conversation_key TEXT NOT NULL,
                    destination_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_status ON events(status, timestamp_ms);

                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_key TEXT NOT NULL,
                    destination_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    raw_text TEXT NOT NULL DEFAULT '',
                    last_message_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'collecting',
                    extraction_json TEXT,
                    issues_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status, last_message_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_conversation
                ON conversations(conversation_key)
                WHERE status IN ('collecting', 'processing');

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    source_index INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    recipient_name TEXT NOT NULL,
                    recipient_phone TEXT NOT NULL,
                    recipient_address TEXT NOT NULL,
                    five_jin_boxes INTEGER NOT NULL,
                    ten_jin_boxes INTEGER NOT NULL,
                    sender_name TEXT NOT NULL,
                    sender_phone TEXT NOT NULL,
                    sender_address TEXT NOT NULL,
                    note TEXT NOT NULL,
                    receipt_note TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    package_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    UNIQUE(conversation_id, source_index)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._migrate_orders_for_batches(db)

    @staticmethod
    def _migrate_orders_for_batches(db: sqlite3.Connection) -> None:
        """將舊版一段訊息一筆訂單的表，安全升級成一對多。"""

        columns = {row[1] for row in db.execute("PRAGMA table_info(orders)")}
        if "source_index" in columns:
            return
        db.executescript(
            """
            ALTER TABLE orders RENAME TO orders_single_backup;
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                source_index INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                recipient_name TEXT NOT NULL,
                recipient_phone TEXT NOT NULL,
                recipient_address TEXT NOT NULL,
                five_jin_boxes INTEGER NOT NULL,
                ten_jin_boxes INTEGER NOT NULL,
                sender_name TEXT NOT NULL,
                sender_phone TEXT NOT NULL,
                sender_address TEXT NOT NULL,
                note TEXT NOT NULL,
                receipt_note TEXT NOT NULL,
                confidence REAL NOT NULL,
                package_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                UNIQUE(conversation_id, source_index)
            );
            INSERT INTO orders (
                id, conversation_id, source_index, created_at, customer_name,
                recipient_name, recipient_phone, recipient_address, five_jin_boxes,
                ten_jin_boxes, sender_name, sender_phone, sender_address, note,
                receipt_note, confidence, package_count, status, raw_text
            )
            SELECT id, conversation_id, 1, created_at, customer_name,
                recipient_name, recipient_phone, recipient_address, five_jin_boxes,
                ten_jin_boxes, sender_name, sender_phone, sender_address, note,
                receipt_note, confidence, package_count, status, raw_text
            FROM orders_single_backup;
            DROP TABLE orders_single_backup;
            """
        )

    def enqueue_event(self, event: dict[str, Any]) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO events
                (event_id, conversation_key, destination_id, user_id, message_id, body,
                 timestamp_ms, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
                (
                    event["event_id"], event["conversation_key"], event["destination_id"],
                    event["user_id"], event["message_id"], event["body"], event["timestamp_ms"],
                    datetime.now(UTC).isoformat(),
                ),
            )
            return cursor.rowcount == 1

    def ingest_queued_events(self, limit: int = 100) -> int:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE status='queued' ORDER BY timestamp_ms LIMIT ?", (limit,)
            ).fetchall()
            for row in rows:
                now = datetime.fromtimestamp(row["timestamp_ms"] / 1000, tz=UTC).isoformat()
                conversation = db.execute(
                    "SELECT id, raw_text FROM conversations WHERE conversation_key=? AND status='collecting'",
                    (row["conversation_key"],),
                ).fetchone()
                if conversation:
                    combined = "\n".join(part for part in (conversation["raw_text"], row["body"]) if part)
                    db.execute(
                        "UPDATE conversations SET raw_text=?, last_message_at=? WHERE id=?",
                        (combined, now, conversation["id"]),
                    )
                else:
                    db.execute(
                        """INSERT INTO conversations
                        (conversation_key, destination_id, user_id, raw_text, last_message_at, status)
                        VALUES (?, ?, ?, ?, ?, 'collecting')""",
                        (row["conversation_key"], row["destination_id"], row["user_id"], row["body"], now),
                    )
                db.execute("UPDATE events SET status='ingested' WHERE event_id=?", (row["event_id"],))
            return len(rows)

    def quiet_conversations(self, quiet_seconds: int, limit: int = 20) -> list[sqlite3.Row]:
        cutoff = (datetime.now(UTC) - timedelta(seconds=quiet_seconds)).isoformat()
        with self.connect() as db:
            return db.execute(
                """SELECT * FROM conversations
                WHERE status='collecting' AND last_message_at<=?
                ORDER BY last_message_at LIMIT ?""",
                (cutoff, limit),
            ).fetchall()

    def mark_processing(self, conversation_id: int) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE conversations SET status='processing' WHERE id=? AND status='collecting'",
                (conversation_id,),
            )
            return cursor.rowcount == 1

    def mark_pending(self, conversation_id: int, extraction: BaseModel | None, issues: list[str]) -> None:
        payload = extraction.model_dump_json() if extraction else None
        with self.connect() as db:
            db.execute(
                "UPDATE conversations SET status='pending_review', extraction_json=?, issues_json=? WHERE id=?",
                (payload, json.dumps(issues, ensure_ascii=False), conversation_id),
            )

    def save_ready_orders(
        self,
        conversation: sqlite3.Row,
        extraction: OrderBatchExtraction,
        rows: list[tuple[CleanOrder, int, str]],
    ) -> list[int]:
        with self.connect() as db:
            order_ids: list[int] = []
            for source_index, (order, package_count, sheet_status) in enumerate(rows, start=1):
                cursor = db.execute(
                    """INSERT INTO orders
                (conversation_id, source_index, created_at, customer_name, recipient_name, recipient_phone,
                 recipient_address, five_jin_boxes, ten_jin_boxes, sender_name, sender_phone,
                 sender_address, note, receipt_note, confidence, package_count, status, raw_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                    conversation["id"], source_index, datetime.now(UTC).isoformat(), order.customer_name,
                    order.recipient_name, order.recipient_phone, order.recipient_address,
                    order.five_jin_boxes, order.ten_jin_boxes, order.sender_name, order.sender_phone,
                    order.sender_address, order.note, order.receipt_note, order.confidence,
                    package_count, sheet_status, conversation["raw_text"],
                    ),
                )
                order_ids.append(int(cursor.lastrowid))
            db.execute(
                "UPDATE conversations SET status='completed', extraction_json=?, issues_json='[]' WHERE id=?",
                (extraction.model_dump_json(), conversation["id"]),
            )
            return order_ids

    def release_processing(self, conversation_id: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE conversations SET status='collecting' WHERE id=? AND status='processing'",
                (conversation_id,),
            )

    def mark_command_completed(self, conversation_id: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE conversations SET status='completed', issues_json='[]' WHERE id=?",
                (conversation_id,),
            )

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, datetime.now(UTC).isoformat()),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def today_summary(self, local_date: str, timezone_name: str = "Asia/Taipei") -> dict[str, int]:
        local_zone = ZoneInfo(timezone_name)
        local_start = datetime.fromisoformat(local_date).replace(tzinfo=local_zone)
        utc_start = local_start.astimezone(UTC)
        utc_end = (local_start + timedelta(days=1)).astimezone(UTC)
        with self.connect() as db:
            rows = db.execute(
                """SELECT five_jin_boxes, ten_jin_boxes, package_count FROM orders
                WHERE created_at>=? AND created_at<?""",
                (utc_start.isoformat(), utc_end.isoformat()),
            ).fetchall()
            pending = db.execute(
                "SELECT COUNT(*) FROM conversations WHERE status='pending_review'"
            ).fetchone()[0]
        return {
            "orders": len(rows),
            "packages": sum(row["package_count"] for row in rows),
            "five": sum(row["five_jin_boxes"] for row in rows),
            "ten": sum(row["ten_jin_boxes"] for row in rows),
            "pending": int(pending),
        }

    def status_counts(self) -> dict[str, int]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS count FROM conversations GROUP BY status"
            ).fetchall()
        return {row["status"]: row["count"] for row in rows}
