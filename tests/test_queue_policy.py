from dataclasses import dataclass
from types import SimpleNamespace

from app.infrastructure.clients import MockDataPlane, MockFrappe
from app.infrastructure.database import fingerprint
from app.application.bridge import Bridge


@dataclass
class Job:
    kind: str
    resource_key: str
    priority: int
    version: int
    payload: dict
    dependencies: list[dict]
    state: str = "pending"
    retry_at: int = 0


class QueueStore:
    """Small deterministic store double for worker ordering/idempotency policy."""

    def __init__(self):
        self.jobs: list[Job] = []
        self.events: dict[str, tuple[str, str, str]] = {}
        self.clock = 0

    def stage(self, kind, key, priority, version, payload, dependencies=None):
        existing = next((job for job in self.jobs if (job.kind, job.resource_key) == (kind, key)), None)
        if existing:
            return "unchanged"
        self.jobs.append(Job(kind, key, priority, version, payload, dependencies or []))
        return "staged"

    def stage_many(self, resources, batch_size=100):
        counts = {"staged": 0, "restaged": 0, "unchanged": 0}
        for item in resources:
            counts[self.stage(*item)] += 1
        return counts

    def claim_config(self):
        ready = sorted((
            job for job in self.jobs
            if job.state == "pending" and job.retry_at <= self.clock
            and all(any(
                parent.kind == dependency["kind"]
                and parent.resource_key == dependency["key"]
                and parent.state == "done"
                for parent in self.jobs
            ) for dependency in job.dependencies)
        ), key=lambda job: (job.priority, job.resource_key))
        if not ready:
            return None
        job = ready[0]
        job.state = "processing"
        return job

    def finish_config(self, kind, key):
        next(job for job in self.jobs if (job.kind, job.resource_key) == (kind, key)).state = "done"

    def fail_config(self, job, error, permanent=False):
        job.state = "dead" if permanent else "pending"
        job.retry_at = self.clock + 10

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
        org_code="demo", org_name="Demo Organization", nextgrp_organization_name="Demo Organization",
        device_status_online="Online", device_status_offline="Offline",
        alarm_status_new="New", alarm_status_cleared="Cleared", alarm_ack_status="Acknowledged",
        alarm_processing_status="In Progress", alarm_closed_status="Closed",
        sync_batch_size=100, inbox_batch_size=100,
    )


class FailsProfileOnce(MockDataPlane):
    def __init__(self):
        super().__init__()
        self.failed = False

    def send(self, kind, resource_key, payload):
        if kind == "profile" and resource_key == "TEMP_HUM_SENSOR" and not self.failed:
            self.failed = True
            raise ConnectionError("temporary Data Plane error")
        return super().send(kind, resource_key, payload)


def test_failed_profile_blocks_only_its_own_device():
    store, plane = QueueStore(), FailsProfileOnce()
    frappe = MockFrappe()
    frappe.docs["Device Profile"].append({
        **frappe.docs["Device Profile"][0], "name": "PROFILE-002", "profile_code": "OTHER_SENSOR",
    })
    frappe.docs["Device"].append({
        **frappe.docs["Device"][0], "name": "DEVICE-002", "device_id": "TH-002", "device_profile": "PROFILE-002",
    })
    frappe.docs["Rule Configuration"].append({
        **frappe.docs["Rule Configuration"][0], "name": "RULE-002", "rule_id": "OTHER-HIGH", "scope_ref": "DEVICE-002",
    })
    bridge = Bridge(cfg(), store, frappe, plane)
    bridge.stage_configuration()

    first = bridge.flush_configuration()
    assert first["failed"] == 1
    assert [(kind, key) for kind, key, _ in plane.sent] == [
        ("organization", "demo"), ("site", "demo-site"), ("zone", "office"),
        ("profile", "OTHER_SENSOR"), ("device", "TH-002"), ("rule", "OTHER-HIGH"),
    ]

    store.clock += 10
    second = bridge.flush_configuration()
    assert second["failed"] == 0
    assert [(kind, key) for kind, key, _ in plane.sent][-3:] == [
        ("profile", "TEMP_HUM_SENSOR"), ("device", "TH-001"), ("rule", "TEMP-HIGH"),
    ]


def test_invalid_device_does_not_block_other_devices():
    store, plane, frappe = QueueStore(), MockDataPlane(), MockFrappe()
    frappe.docs["Device"].append({
        **frappe.docs["Device"][0], "name": "DEVICE-BROKEN", "device_id": "TH-BROKEN",
        "device_profile": "MISSING-PROFILE",
    })
    bridge = Bridge(cfg(), store, frappe, plane)

    staged = bridge.stage_configuration()
    delivered = bridge.flush_configuration()

    assert staged["invalid"][0]["name"] == "DEVICE-BROKEN"
    assert staged["invalid"][0]["kind"] == "device"
    assert delivered["failed"] == 0
    assert ("device", "TH-001") in [(kind, key) for kind, key, _ in plane.sent]
    assert ("device", "TH-BROKEN") not in [(kind, key) for kind, key, _ in plane.sent]


def test_alarm_ack_is_stable_for_a_duplicate_event_id():
    bridge = Bridge(cfg(), QueueStore(), MockFrappe(), MockDataPlane())
    body = {
        "event_id": "bridge-alarm-1", "org_code": "demo", "alarm_key": "rule:device:1", "episode_key": "rule:device:1", "kind": "occurrence",
        "rule_id": "TEMP-HIGH", "rule_version": 1, "device_id": "TH-001", "episode_start": "2026-10-22T03:00:00Z", "occurrence_seq": 1, "raised_at": "2026-10-22T03:00:00Z",
        "event": {"event_id": "source-1", "event_type": "TEMPERATURE_HIGH", "ts": "2026-10-22T03:00:00Z"},
    }
    first, second = bridge.receive_alarm(body), bridge.receive_alarm(body)
    assert first == {"accepted": True, "duplicate": False, "ack_id": "ack-1", "alarm_key": "rule:device:1"}
    assert second == {**first, "duplicate": True}
