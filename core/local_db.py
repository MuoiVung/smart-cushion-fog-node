"""
Local SQLite Database for the Smart Cushion Fog Node.

Serves two purposes:
  1. Config Store  – safely persists AI model/scaler paths without editing .env
  2. Offline Cloud Queue – buffers failed cloud events for retry when reconnected

Database file: data/fog_local.db (auto-created on first run)
No external dependencies – uses Python's built-in sqlite3 module.
"""

import logging
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import utils.paths as paths

logger = logging.getLogger(__name__)

_DB_PATH = paths.get_db_path()


class LocalDB:
    """
    Thread-safe SQLite wrapper shared by the Fog Node and the Launcher.

    Both processes access the same file on disk. SQLite WAL mode is enabled
    so concurrent reads from the Launcher and writes from the Fog Node
    do not block each other.

    Usage:
        db = LocalDB()
        db.set_config("model_path", "ai/models/posture_9_model_mix.h5")
        db.enqueue("event", "cushion/device/event", '{"type": "session_start"}')
    """

    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── Schema ─────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")  # Wait up to 10s on lock
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS fog_config (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pending_cloud_queue (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_type TEXT NOT NULL,
                    topic       TEXT NOT NULL,
                    payload     TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    last_retry  TEXT
                );
            """)
        logger.debug(f"LocalDB ready at {self._path}")

    # ── Config Store ───────────────────────────────────────────────────────

    def get_config(self, key: str, default: str = "") -> str:
        """Read a config value; returns `default` if key not set."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM fog_config WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_config(self, key: str, value: str) -> None:
        """Insert or update a config key-value pair."""
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO fog_config (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE
                   SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, value, now),
            )

    # ── Cloud Queue ────────────────────────────────────────────────────────

    def enqueue(self, record_type: str, topic: str, payload_json: str) -> int:
        """Add a failed cloud publish to the pending queue.
        Retries up to 3 times on transient disk I/O errors (Docker bind mount on macOS).
        """
        import time
        now = _now_iso()
        for attempt in range(3):
            try:
                with self._lock, self._connect() as conn:
                    cur = conn.execute(
                        """INSERT INTO pending_cloud_queue
                           (record_type, topic, payload, created_at, retry_count)
                           VALUES (?, ?, ?, ?, 0)""",
                        (record_type, topic, payload_json, now),
                    )
                    row_id = cur.lastrowid
                logger.debug(f"[LocalDB] Enqueued {record_type} (id={row_id})")
                return row_id
            except sqlite3.OperationalError as e:
                if attempt < 2:
                    logger.warning(f"[LocalDB] enqueue retry {attempt+1}/3 due to: {e}")
                    time.sleep(0.2 * (attempt + 1))
                else:
                    logger.error(f"[LocalDB] enqueue failed after 3 retries: {e}")
                    raise
        return -1

    def get_pending(self, limit: int = 100) -> list[dict]:
        """Return up to `limit` oldest pending events ordered by creation time."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT id, record_type, topic, payload, created_at, retry_count
                   FROM pending_cloud_queue
                   ORDER BY created_at ASC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_sent(self, row_id: int) -> None:
        """Delete a row that was successfully sent to the cloud."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM pending_cloud_queue WHERE id = ?", (row_id,)
            )

    def increment_retry(self, row_id: int) -> None:
        """Bump retry_count and record last_retry timestamp."""
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE pending_cloud_queue
                   SET retry_count = retry_count + 1, last_retry = ?
                   WHERE id = ?""",
                (now, row_id),
            )

    def get_pending_count(self) -> int:
        """Return total number of queued (unsent) events."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM pending_cloud_queue"
            ).fetchone()
            return row["cnt"]

    def get_oldest_pending_age_hours(self) -> Optional[float]:
        """Return age in hours of the oldest pending event, or None if queue is empty."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT MIN(created_at) AS oldest FROM pending_cloud_queue"
            ).fetchone()
            if not row or not row["oldest"]:
                return None
            oldest = datetime.fromisoformat(row["oldest"])
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - oldest).total_seconds() / 3600

    def purge_old(self, days: int) -> int:
        """Delete rows older than `days` days. Returns number of deleted rows."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM pending_cloud_queue WHERE created_at < ?", (cutoff,)
            )
            deleted = cur.rowcount
        if deleted:
            logger.info(f"[LocalDB] Purged {deleted} events older than {days} days")
        return deleted

    def purge_all(self) -> int:
        """Delete ALL pending events. Returns number deleted."""
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM pending_cloud_queue")
            deleted = cur.rowcount
        logger.info(f"[LocalDB] Purged all {deleted} pending events")
        return deleted


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
