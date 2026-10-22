from dataclasses import dataclass
from types import SimpleNamespace

from app.adapters import MockDataPlane, MockFrappe
from app.db import fingerprint
from app.service import Bridge


@dataclass
class Job:
    kind: str
    resource_key: str
    priority: int
    version: int
    payload: dict
    state: str = "pending"


class QueueStore:
    """Small deterministic store double for worker ordering/idempotency policy."""

    def __init__(self):
        self.jobs: list[Job] = []
        self.events: dict[str, tuple[str, str, str]] = {}

    def stage(self, kind, key, priority, version, payload):
        existing = next((job for job in self.jobs if (job.kind, job.resource_key) == (kind, key)), None)
        if existing:
            return "unchanged"
        self.jobs.append(Job(kind, key, priority, version, payload))
        return "staged"

    def claim_config(self):
        outstanding = sorted((job for job in self.jobs if job.state != "done"), key=lambda job: (job.priority, job.resource_key))
        if not outstanding or outstanding[0].state != "pending":
            return None
        job = outstanding[0]
        job.state = "processing"
        return job

    def finish_config(self, kind, key):
        next(job for job in self.jobs if (job.kind, job.resource_key) == (kind, key)).state = "done"

    def fail_config(self, job, error, permanent=False):
        job.state = "dead" if permanent else "pending"

    def accept_event(self, event_id, kind, payload):
        digest = fingerprint(payload)
        if event_id in self.events:
            saved_kind, saved_digest, ack_id = self.events[event_id]
            if (saved_kind, saved_digest) != (kind, digest):
                raise ValueError("event_id was reused with a different event")
            return ack_id, True
        ack_id = f"ack-{len(self.events) + 1}"
        self.events[event_id] = (kind, digest, ack_id)
        return ack_id, False


def cfg():
    return SimpleNamespace(
        org_code="demo", org_name="Demo Organization", device_status_online="Online", device_status_offline="Offline",
        alarm_status_new="New", alarm_status_cleared="Cleared", alarm_ack_status="Acknowledged",
        alarm_processing_status="In Progress", alarm_closed_status="Closed",
    )


class FailsProfileOnce(MockDataPlane):
    def __init__(self):
        super().__init__()
        self.failed = False

    def send(self, kind, resource_key, payload):
        if kind == "profile" and not self.failed:
            self.failed = True
            raise ConnectionError("temporary Data Plane error")
        return super().send(kind, resource_key, payload)


def test_profile_retry_blocks_device_and_rule_until_it_succeeds():
    store, plane = QueueStore(), FailsProfileOnce()
    bridge = Bridge(cfg(), store, MockFrappe(), plane)
    bridge.stage_configuration()

    first = bridge.flush_configuration()
    assert first["failed"] == 1
    assert [kind for kind, _, _ in plane.sent] == ["organization", "site", "zone"]

    second = bridge.flush_configuration()
    assert second["failed"] == 0
    assert [kind for kind, _, _ in plane.sent] == ["organization", "site", "zone", "profile", "device", "rule"]


def test_alarm_ack_is_stable_for_a_duplicate_event_id():
    bridge = Bridge(cfg(), QueueStore(), MockFrappe(), MockDataPlane())
    body = {
        "event_id": "bridge-alarm-1", "org_code": "demo", "alarm_key": "rule:device:1", "kind": "occurrence",
        "rule_id": "TEMP-HIGH", "device_id": "TH-001", "raised_at": "2026-10-22T03:00:00Z",
        "event": {"event_id": "source-1", "event_type": "TEMPERATURE_HIGH"},
    }
    first, second = bridge.receive_alarm(body), bridge.receive_alarm(body)
    assert first == {"accepted": True, "duplicate": False, "ack_id": "ack-1", "alarm_key": "rule:device:1"}
    assert second == {**first, "duplicate": True}
