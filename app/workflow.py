"""Lightweight durable workflow state machine with SQLite WAL journal.

Provides idempotent activity execution, checkpointing, and automatic resumption
for the fund close pipeline without requiring external daemons like Temporal or Celery.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

T = TypeVar("T")



def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def canonical_json(data: Any) -> str:
    """Produces deterministic canonical JSON string for hashing and persistence."""
    return json.dumps(data, default=_json_default, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


class WorkflowEngine:
    """Embedded, ACID-compliant activity state engine backed by SQLite in WAL mode."""

    def __init__(self, db_path: Path | str, workflow_id: str = "gpf_close", clean: bool = False) -> None:
        self.db_path = Path(db_path).resolve()
        self.workflow_id = workflow_id
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
        self._init_db()
        if clean:
            self.clear()

    def _init_db(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA busy_timeout = 5000;")
            self._conn.execute("PRAGMA synchronous = NORMAL;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS activities (
                    workflow_id TEXT NOT NULL,
                    activity_name TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output_json TEXT,
                    error_text TEXT,
                    attempt_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (workflow_id, activity_name)
                );
                """
            )

    def compute_input_hash(self, activity_name: str, *args: Any, **kwargs: Any) -> str:
        payload = {"activity": activity_name, "args": args, "kwargs": kwargs}
        raw_bytes = canonical_json(payload).encode("utf-8")
        return hashlib.sha256(raw_bytes).hexdigest()

    def get_activity_record(self, activity_name: str) -> dict[str, Any] | None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT activity_name, input_hash, status, output_json, error_text, attempt_count, created_at, updated_at
                FROM activities
                WHERE workflow_id = ? AND activity_name = ?
                """,
                (self.workflow_id, activity_name),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "activity_name": row[0],
            "input_hash": row[1],
            "status": row[2],
            "output_json": row[3],
            "error_text": row[4],
            "attempt_count": row[5],
            "created_at": row[6],
            "updated_at": row[7],
        }

    def is_completed(self, activity_name: str) -> bool:
        record = self.get_activity_record(activity_name)
        return bool(record and record["status"] == "COMPLETED")

    def run_activity(
        self,
        activity_name: str,
        fn: Callable[..., T],
        *args: Any,
        serializer: Callable[[T], Any] | None = None,
        deserializer: Callable[[Any], T] | None = None,
        max_retries: int = 3,
        initial_delay: float = 0.25,
        backoff_factor: float = 2.0,
        **kwargs: Any,
    ) -> T:
        """Executes an activity idempotently with automatic retry and durable caching."""
        input_hash = self.compute_input_hash(activity_name, *args, **kwargs)
        record = self.get_activity_record(activity_name)

        if record and record["status"] == "COMPLETED" and record["input_hash"] == input_hash:
            cached_raw = json.loads(record["output_json"]) if record["output_json"] is not None else None
            return deserializer(cached_raw) if deserializer else cached_raw

        attempts = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        delay = initial_delay

        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO activities (workflow_id, activity_name, input_hash, status, error_text, attempt_count, created_at, updated_at)
                VALUES (?, ?, ?, 'RUNNING', NULL, 1, ?, ?)
                ON CONFLICT(workflow_id, activity_name) DO UPDATE SET
                    input_hash = excluded.input_hash,
                    status = 'RUNNING',
                    error_text = NULL,
                    attempt_count = attempt_count + 1,
                    updated_at = excluded.updated_at
                """,
                (self.workflow_id, activity_name, input_hash, now_iso, now_iso),
            )

        while True:
            attempts += 1
            try:
                result = fn(*args, **kwargs)
                serializable = serializer(result) if serializer else result
                output_json = canonical_json(serializable)
                finished_at = datetime.now(timezone.utc).isoformat()
                with self._lock, self._conn:
                    self._conn.execute(
                        """
                        UPDATE activities
                        SET status = 'COMPLETED', output_json = ?, error_text = NULL, updated_at = ?
                        WHERE workflow_id = ? AND activity_name = ?
                        """,
                        (output_json, finished_at, self.workflow_id, activity_name),
                    )
                return result
            except Exception as exc:
                if attempts >= max_retries:
                    failed_at = datetime.now(timezone.utc).isoformat()
                    with self._lock, self._conn:
                        self._conn.execute(
                            """
                            UPDATE activities
                            SET status = 'FAILED', error_text = ?, updated_at = ?
                            WHERE workflow_id = ? AND activity_name = ?
                            """,
                            (str(exc), failed_at, self.workflow_id, activity_name),
                        )
                    raise
                time.sleep(delay)
                delay *= backoff_factor

    def clear(self) -> None:
        """Purges all activity records for this workflow."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM activities WHERE workflow_id = ?", (self.workflow_id,))

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


    def __enter__(self) -> WorkflowEngine:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
