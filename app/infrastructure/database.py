import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Index, Integer, String, Text, create_engine, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.application.errors import EventIdConflictError, StorageUnavailableError


def now() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class Base(DeclarativeBase):
    pass


class ConfigJob(Base):
    __tablename__ = "config_jobs"
    __table_args__ = (Index("ix_config_jobs_ready", "state", "retry_at", "priority"),)

    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    resource_key: Mapped[str] = mapped_column(String(250), primary_key=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    dependencies: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
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


class DeviceStatusCursor(Base):
    """Last status-transition timestamp applied for a device."""

    __tablename__ = "device_status_cursors"

    org_code: Mapped[str] = mapped_column(String(120), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    state_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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

    def recover_processing(self):
        """Call under cycle_lock to resume work interrupted by a process crash."""
        with self.sessions.begin() as session:
            session.execute(update(ConfigJob).where(ConfigJob.state == "processing").values(state="pending", retry_at=now()))
            session.execute(update(InboxEvent).where(InboxEvent.state == "processing").values(state="pending", retry_at=now()))

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

    def stage(
        self, kind: str, resource_key: str, priority: int, version: int,
        payload: dict, dependencies: list[dict] | None = None,
    ) -> str:
        with self.sessions.begin() as session:
            return self._stage(session, kind, resource_key, priority, version, payload, dependencies or [])

    @staticmethod
    def _stage(session, kind, resource_key, priority, version, payload, dependencies):
        row = session.get(ConfigJob, (kind, resource_key))
        outcome, _ = Store._stage_row(session, row, kind, resource_key, priority, version, payload, dependencies)
        return outcome

    @staticmethod
    def _stage_row(session, row, kind, resource_key, priority, version, payload, dependencies):
        digest = fingerprint(payload)
        if row is None:
            row = ConfigJob(
                kind=kind, resource_key=resource_key, priority=priority, version=version,
                payload=payload, fingerprint=digest, dependencies=dependencies,
            )
            session.add(row)
            return "staged", row
        if version < row.version:
            raise ValueError(f"{kind}/{resource_key}: version decreased ({version} < {row.version})")
        if version == row.version and digest != row.fingerprint:
            raise ValueError(f"{kind}/{resource_key}: payload changed without version increase")
        if version == row.version:
            if row.dependencies != dependencies:
                row.dependencies = dependencies
            return "unchanged", row
        row.priority, row.version, row.payload, row.fingerprint, row.dependencies = (
            priority, version, payload, digest, dependencies,
        )
        row.state, row.attempts, row.retry_at, row.last_error = "pending", 0, now(), None
        return "restaged", row

    def stage_many(self, resources, batch_size: int = 100) -> dict:
        """Prefetch each batch, then stage valid resources in a bounded transaction."""
        counts = {"staged": 0, "restaged": 0, "unchanged": 0, "invalid": []}
        for start in range(0, len(resources), batch_size):
            batch = resources[start:start + batch_size]
            with self.sessions.begin() as session:
                keys = {(item[0], item[1]) for item in batch}
                existing = session.execute(
                    select(ConfigJob).where(tuple_(ConfigJob.kind, ConfigJob.resource_key).in_(keys))
                ).scalars().all()
                rows = {(row.kind, row.resource_key): row for row in existing}
                for kind, key, priority, version, payload, dependencies in batch:
                    try:
                        outcome, row = Store._stage_row(
                            session, rows.get((kind, key)), kind, key,
                            priority, version, payload, dependencies,
                        )
                    except ValueError as exc:
                        counts["invalid"].append({"kind": kind, "key": key, "error": str(exc)})
                        continue
                    counts[outcome] += 1
                    rows[(kind, key)] = row
        return counts

    def claim_config(self) -> ConfigJob | None:
        with self.sessions.begin() as session:
            # PostgreSQL checks dependencies before claiming. A failed Profile
            # blocks its Devices, but a Device using another Profile may proceed.
            key = session.execute(text("""
                SELECT job.kind, job.resource_key
                FROM config_jobs AS job
                WHERE job.state = 'pending' AND job.retry_at <= clock_timestamp()
                  AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(job.dependencies) AS dependency(item)
                    LEFT JOIN config_jobs AS parent
                      ON parent.kind = dependency.item->>'kind'
                     AND parent.resource_key = dependency.item->>'key'
                    WHERE parent.state IS DISTINCT FROM 'done'
                  )
                ORDER BY job.priority, job.resource_key
                FOR UPDATE OF job SKIP LOCKED
                LIMIT 1
            """)).first()
            if key is None:
                return None
            row = session.get(ConfigJob, (key.kind, key.resource_key))
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

    def list_config(self, state: str, limit: int = 100) -> list[dict]:
        with self.sessions() as session:
            rows = session.execute(
                select(ConfigJob).where(ConfigJob.state == state)
                .order_by(ConfigJob.priority, ConfigJob.resource_key).limit(limit)
            ).scalars().all()
            parent_keys = {
                (dependency["kind"], dependency["key"])
                for row in rows for dependency in row.dependencies
            }
            parent_states = {}
            if parent_keys:
                parents = session.execute(
                    select(ConfigJob.kind, ConfigJob.resource_key, ConfigJob.state)
                    .where(tuple_(ConfigJob.kind, ConfigJob.resource_key).in_(parent_keys))
                ).all()
                parent_states = {(parent.kind, parent.resource_key): parent.state for parent in parents}
            output = []
            for row in rows:
                blocked_by = []
                for dependency in row.dependencies:
                    parent_state = parent_states.get((dependency["kind"], dependency["key"]))
                    if parent_state != "done":
                        blocked_by.append({**dependency, "state": parent_state or "missing"})
                output.append({
                    "kind": row.kind, "key": row.resource_key,
                    "version": row.version, "state": row.state,
                    "dependencies": row.dependencies, "attempts": row.attempts,
                    "retry_at": row.retry_at, "last_error": row.last_error,
                    "blocked_by": blocked_by,
                })
            return output

    def retry_config(self, kind: str, resource_key: str) -> bool:
        with self.sessions.begin() as session:
            row = session.get(ConfigJob, (kind, resource_key))
            if row is None or row.state != "dead":
                return False
            row.state, row.attempts, row.retry_at, row.last_error = "pending", 0, now(), None
            return True

    def accept_event(self, event_id: str, kind: str, payload: dict) -> tuple[str, bool]:
        digest = fingerprint(payload)
        try:
            with self.sessions.begin() as session:
                row = session.get(InboxEvent, event_id)
                if row:
                    if row.kind != kind or row.fingerprint != digest:
                        raise EventIdConflictError("EVENT_ID_CONFLICT")
                    return row.ack_id, True
                ack_id = str(uuid.uuid4())
                session.add(InboxEvent(event_id=event_id, ack_id=ack_id, kind=kind, payload=payload, fingerprint=digest))
                return ack_id, False
        except IntegrityError:
            # Another request committed the same event_id after our initial read.
            # The durable unique key remains the source of truth for this ACK.
            try:
                with self.sessions() as session:
                    row = session.get(InboxEvent, event_id)
                    if row and row.kind == kind and row.fingerprint == digest:
                        return row.ack_id, True
                    if row:
                        raise EventIdConflictError("EVENT_ID_CONFLICT")
            except SQLAlchemyError as exc:
                raise StorageUnavailableError("Inbox temporarily unavailable") from exc
            raise StorageUnavailableError("Inbox event could not be committed")
        except SQLAlchemyError as exc:
            raise StorageUnavailableError("Inbox temporarily unavailable") from exc

    def status_is_stale(self, org_code: str, device_id: str, state_at: datetime) -> bool:
        with self.sessions() as session:
            cursor = session.get(DeviceStatusCursor, (org_code, device_id))
            return bool(cursor and state_at <= cursor.state_at)

    def save_status_cursor(self, org_code: str, device_id: str, state: str, state_at: datetime):
        with self.sessions.begin() as session:
            cursor = session.get(DeviceStatusCursor, (org_code, device_id))
            if cursor is None:
                session.add(DeviceStatusCursor(org_code=org_code, device_id=device_id, state=state, state_at=state_at))
            elif state_at > cursor.state_at:
                cursor.state, cursor.state_at = state, state_at

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

    def retry_inbox(self, event_id: str) -> bool:
        with self.sessions.begin() as session:
            row = session.get(InboxEvent, event_id)
            if row is None or row.state != "dead":
                return False
            row.state, row.attempts, row.retry_at, row.last_error = "pending", 0, now(), None
            return True
