import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Integer, String, Text, create_engine, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class Base(DeclarativeBase):
    pass


class ConfigJob(Base):
    __tablename__ = "config_jobs"

    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    resource_key: Mapped[str] = mapped_column(String(250), primary_key=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)


class InboxEvent(Base):
    __tablename__ = "inbox_events"

    event_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    ack_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)


class Store:
    """Durable outgoing queue and at-least-once incoming event inbox."""

    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, pool_pre_ping=True)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def close(self):
        self.engine.dispose()

    def health(self):
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    @contextmanager
    def cycle_lock(self):
        """Hold a PostgreSQL advisory lock while a complete bridge cycle runs."""
        with self.engine.connect() as connection:
            acquired = connection.execute(text("SELECT pg_try_advisory_lock(hashtext('nextwireless_bridge_cycle'))")).scalar_one()
            try:
                yield bool(acquired)
            finally:
                if acquired:
                    connection.execute(text("SELECT pg_advisory_unlock(hashtext('nextwireless_bridge_cycle'))"))

    def stage(self, kind: str, resource_key: str, priority: int, version: int, payload: dict) -> str:
        digest = fingerprint(payload)
        with self.sessions.begin() as session:
            row = session.get(ConfigJob, (kind, resource_key))
            if row is None:
                session.add(ConfigJob(kind=kind, resource_key=resource_key, priority=priority, version=version, payload=payload, fingerprint=digest))
                return "staged"
            if version < row.version:
                raise ValueError(f"{kind}/{resource_key}: version decreased ({version} < {row.version})")
            if version == row.version and digest != row.fingerprint:
                raise ValueError(f"{kind}/{resource_key}: payload changed without version increase")
            if version == row.version:
                return "unchanged"
            row.priority, row.version, row.payload, row.fingerprint = priority, version, payload, digest
            row.state, row.attempts, row.retry_at, row.last_error = "pending", 0, now(), None
            return "restaged"

    def claim_config(self) -> ConfigJob | None:
        with self.sessions.begin() as session:
            # An unresolved lower-priority item deliberately blocks dependants.
            # Sending a Device after its Profile has failed would violate IF-01/02.
            row = session.execute(
                select(ConfigJob).where(ConfigJob.state != "done").order_by(ConfigJob.priority, ConfigJob.resource_key).with_for_update(skip_locked=True)
            ).scalars().first()
            if row is None or row.state != "pending" or row.retry_at > now():
                return None
            row.state = "processing"
            session.flush()
            session.expunge(row)
            return row

    def finish_config(self, kind: str, resource_key: str):
        with self.sessions.begin() as session:
            row = session.get(ConfigJob, (kind, resource_key))
            if row:
                row.state, row.last_error = "done", None

    def fail_config(self, job: ConfigJob, error: Exception, permanent: bool = False):
        with self.sessions.begin() as session:
            row = session.get(ConfigJob, (job.kind, job.resource_key))
            if row is None:
                return
            row.attempts += 1
            row.last_error = str(error)[:2000]
            row.state = "dead" if permanent or row.attempts >= 8 else "pending"
            row.retry_at = now() + timedelta(seconds=min(300, 2 ** row.attempts))

    def accept_event(self, event_id: str, kind: str, payload: dict) -> tuple[str, bool]:
        digest = fingerprint(payload)
        with self.sessions.begin() as session:
            row = session.get(InboxEvent, event_id)
            if row:
                if row.kind != kind or row.fingerprint != digest:
                    raise ValueError("event_id was reused with a different event")
                return row.ack_id, True
            ack_id = str(uuid.uuid4())
            session.add(InboxEvent(event_id=event_id, ack_id=ack_id, kind=kind, payload=payload, fingerprint=digest))
            return ack_id, False

    def claim_inbox(self) -> InboxEvent | None:
        with self.sessions.begin() as session:
            row = session.execute(
                select(InboxEvent).where(InboxEvent.state == "pending", InboxEvent.retry_at <= now()).order_by(InboxEvent.created_at).with_for_update(skip_locked=True)
            ).scalars().first()
            if row is None:
                return None
            row.state = "processing"
            session.flush()
            session.expunge(row)
            return row

    def finish_inbox(self, event_id: str):
        with self.sessions.begin() as session:
            row = session.get(InboxEvent, event_id)
            if row:
                row.state, row.last_error, row.processed_at = "done", None, now()

    def fail_inbox(self, event: InboxEvent, error: Exception, permanent: bool = False):
        with self.sessions.begin() as session:
            row = session.get(InboxEvent, event.event_id)
            if row is None:
                return
            row.attempts += 1
            row.last_error = str(error)[:2000]
            row.state = "dead" if permanent or row.attempts >= 8 else "pending"
            row.retry_at = now() + timedelta(seconds=min(300, 2 ** row.attempts))

    def list_inbox(self, state: str, limit: int = 100) -> list[dict]:
        with self.sessions() as session:
            rows = session.execute(select(InboxEvent).where(InboxEvent.state == state).order_by(InboxEvent.created_at.desc()).limit(limit)).scalars()
            return [{"event_id": row.event_id, "ack_id": row.ack_id, "kind": row.kind, "state": row.state, "attempts": row.attempts, "last_error": row.last_error, "created_at": row.created_at, "processed_at": row.processed_at} for row in rows]
