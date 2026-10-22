import asyncio
import hashlib
import json
import sqlite3
import time

from pathlib import Path


def canonical(obj: dict) -> str:
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":")
    )


def digest(obj: dict) -> str:
    return hashlib.sha256(
        canonical(obj).encode()
    ).hexdigest()


class StateStore:
    """
    SQLite durable inbox and sync cursor.
    Designed for one M1 bridge worker process.
    """

    def __init__(self, filename: str):
        self.filename = filename

    async def _run(self, operation):
        def execute():
            db = sqlite3.connect(
                self.filename,
                timeout=10
            )
            try:
                db.execute(
                    "PRAGMA busy_timeout=10000"
                )
                return operation(db)
            finally:
                db.close()

        return await asyncio.to_thread(execute)

    async def connect(self):
        Path(self.filename).parent.mkdir(
            parents=True,
            exist_ok=True
        )

        def init(db):
            db.execute(
                "PRAGMA journal_mode=WAL"
            )

            with db:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS sync_state (
                        kind TEXT NOT NULL,
                        item_key TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        hash TEXT NOT NULL,
                        PRIMARY KEY(kind, item_key)
                    )
                """)

                db.execute("""
                    CREATE TABLE IF NOT EXISTS inbox (
                        event_id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        state TEXT NOT NULL
                            DEFAULT 'pending',
                        attempts INTEGER NOT NULL
                            DEFAULT 0,
                        available_at REAL NOT NULL
                            DEFAULT 0,
                        error TEXT NOT NULL
                            DEFAULT ''
                    )
                """)

        await self._run(init)

    async def sync_entry(
        self,
        kind,
        key
    ):
        return await self._run(
            lambda db: db.execute(
                """
                SELECT version, hash
                FROM sync_state
                WHERE kind=? AND item_key=?
                """,
                (kind, key)
            ).fetchone()
        )

    async def save_sync(
        self,
        kind,
        key,
        version,
        fingerprint
    ):
        def save(db):
            with db:
                db.execute(
                    """
                    INSERT INTO sync_state
                        (kind, item_key, version, hash)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(kind, item_key)
                    DO UPDATE SET
                        version=excluded.version,
                        hash=excluded.hash
                    """,
                    (
                        kind,
                        key,
                        version,
                        fingerprint
                    )
                )

        await self._run(save)

    async def enqueue(
        self,
        event_id,
        kind,
        payload
    ):
        raw = canonical(payload)

        def insert(db):
            with db:
                db.execute(
                    "BEGIN IMMEDIATE"
                )

                existing = db.execute(
                    """
                    SELECT payload
                    FROM inbox
                    WHERE event_id=?
                    """,
                    (event_id,)
                ).fetchone()

                if existing:
                    if existing[0] != raw:
                        raise ValueError(
                            "Conflicting payload "
                            "for existing event_id"
                        )

                    return False

                db.execute(
                    """
                    INSERT INTO inbox
                        (event_id, kind, payload)
                    VALUES (?, ?, ?)
                    """,
                    (
                        event_id,
                        kind,
                        raw
                    )
                )

                return True

        return await self._run(insert)

    async def claim(self):
        def claim_one(db):
            now = time.time()

            with db:
                db.execute(
                    "BEGIN IMMEDIATE"
                )

                row = db.execute(
                    """
                    SELECT
                        event_id,
                        kind,
                        payload,
                        attempts
                    FROM inbox
                    WHERE state IN (
                        'pending',
                        'processing'
                    )
                    AND available_at <= ?
                    ORDER BY available_at, event_id
                    LIMIT 1
                    """,
                    (now,)
                ).fetchone()

                if row:
                    db.execute(
                        """
                        UPDATE inbox
                        SET state='processing',
                            available_at=?
                        WHERE event_id=?
                        """,
                        (
                            now + 120,
                            row[0]
                        )
                    )

            if not row:
                return None

            return (
                row[0],
                row[1],
                json.loads(row[2]),
                row[3]
            )

        return await self._run(claim_one)

    async def finish(self, event_id):
        def finish_one(db):
            with db:
                db.execute(
                    """
                    UPDATE inbox
                    SET state='done',
                        error=''
                    WHERE event_id=?
                    """,
                    (event_id,)
                )

        await self._run(finish_one)

    async def fail(
        self,
        event_id,
        attempts,
        exc,
        permanent=False
    ):
        attempts += 1

        dead = (
            permanent
            or attempts >= 8
        )

        wait = min(
            300,
            2 ** min(attempts, 8)
        )

        def fail_one(db):
            with db:
                db.execute(
                    """
                    UPDATE inbox
                    SET state=?,
                        attempts=?,
                        available_at=?,
                        error=?
                    WHERE event_id=?
                    """,
                    (
                        "dead" if dead else "pending",
                        attempts,
                        time.time() + wait,
                        str(exc)[:500],
                        event_id
                    )
                )

        await self._run(fail_one)

    async def list_inbox(
        self,
        state="dead"
    ):
        def fetch(db):
            return db.execute(
                """
                SELECT
                    event_id,
                    kind,
                    state,
                    attempts,
                    error
                FROM inbox
                WHERE state=?
                ORDER BY event_id
                LIMIT 100
                """,
                (state,)
            ).fetchall()

        rows = await self._run(fetch)

        columns = (
            "event_id",
            "kind",
            "state",
            "attempts",
            "error"
        )

        return [
            dict(zip(columns, row))
            for row in rows
        ]

    async def retry(self, event_id):
        def retry_one(db):
            with db:
                cursor = db.execute(
                    """
                    UPDATE inbox
                    SET state='pending',
                        attempts=0,
                        available_at=0,
                        error=''
                    WHERE event_id=?
                    AND state='dead'
                    """,
                    (event_id,)
                )

                return cursor.rowcount > 0

        return await self._run(retry_one)