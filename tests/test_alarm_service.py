from types import SimpleNamespace

from app.infrastructure.clients import MockDataPlane, MockFrappe
from app.application.bridge import Bridge


def config():
    return SimpleNamespace(
        org_code="demo",
        org_name="Demo Organization",
        device_status_online="Online",
        device_status_offline="Offline",
        alarm_status_new="New",
        alarm_status_cleared="Cleared",
        alarm_ack_status="Acknowledged",
        alarm_processing_status="In Progress",
        alarm_closed_status="Closed",
    )


def occurrence(event_id="bridge-1", sequence=1):
    return {
        "event_id": event_id,
        "org_code": "demo",
        "alarm_key": "RULE-TEMP-HIGH:TH-001:1792638000",
        "episode_key": "RULE-TEMP-HIGH:TH-001:1792638000",
        "kind": "occurrence",
        "rule_id": "TEMP-HIGH",
        "rule_version": 1,
        "device_id": "TH-001",
        "zone_code": "office",
        "severity": "High",
        "episode_start": "2026-10-22T03:00:00Z",
        "raised_at": "2026-10-22T03:01:00Z",
        "occurrence_seq": sequence,
        "event": {"event_id": "sensor-1", "event_type": "TEMPERATURE_HIGH", "ts": "2026-10-22T03:01:00Z", "data": {"temperature": 31}},
    }


def test_quiet_window_occurrences_update_one_alarm_and_keep_source_event_separate():
    frappe = MockFrappe()
    bridge = Bridge(config(), None, frappe, MockDataPlane())

    assert bridge.apply_alarm(occurrence()) == "created-occurrence"
    assert bridge.apply_alarm(occurrence("bridge-2", 2)) == "updated-occurrence"

    saved = frappe.find("Alarm", "alarm_key", "RULE-TEMP-HIGH:TH-001:1792638000")
    assert frappe.creates == 1
    assert saved["occurrence_count"] == 2
    assert saved["source_event_id"] == "sensor-1"
    assert saved["event_type"] == "TEMPERATURE_HIGH"


def test_cleared_is_idempotent_and_auto_closes_from_rule_configuration():
    frappe = MockFrappe()
    bridge = Bridge(config(), None, frappe, MockDataPlane())
    bridge.apply_alarm(occurrence())

    clear = {**occurrence("bridge-clear"), "kind": "cleared", "cleared_at": "2026-10-22T03:11:00Z"}
    clear.pop("raised_at")
    assert bridge.apply_alarm(clear) == "cleared"
    assert bridge.apply_alarm(clear) == "already-cleared"
    recurrence = occurrence("bridge-recurrence", 2)
    recurrence["raised_at"] = "2026-10-22T03:12:00Z"
    recurrence["event"]["ts"] = "2026-10-22T03:12:00Z"
    assert bridge.apply_alarm(recurrence) == "updated-occurrence"

    saved = frappe.find("Alarm", "alarm_key", clear["alarm_key"])
    assert saved["status"] == "New"
    assert saved["cleared_at"] is None
