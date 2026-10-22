"""Deterministic in-memory adapters used in local development and unit tests."""

from copy import deepcopy
from datetime import datetime, timezone


MOCK_DOCS = {
    "Site": [{"name": "SITE-001", "site_code": "demo-site", "site_name": "Demo Site", "version": 1}],
    "Zone": [{"name": "ZONE-001", "zone_code": "office", "zone_name": "Office", "site": "SITE-001", "version": 1}],
    "Device Profile": [{
        "name": "PROFILE-001", "profile_code": "TEMP_HUM_SENSOR", "version": 1,
        "signal_model": "TELEMETRY", "protocol": "MQTT", "expected_interval_s": 60,
        "heartbeat_interval_s": 60, "offline_after_missed": 3,
        "event_types": [
            {"event_code": "TEMPERATURE_HIGH", "event_name": "Temperature high"},
            {"event_code": "TEMPERATURE_NORMAL", "event_name": "Temperature normal"},
        ],
        "profile_data_points": [
            {"code": "temperature", "label": "Temperature", "data_type": "Float", "channel": "telemetry", "unit": "Cel", "min": -20, "max": 80, "decimal_places": 1, "is_required": 1},
            {"code": "humidity", "label": "Humidity", "data_type": "Float", "unit": "%", "min": 0, "max": 100, "decimal_places": 1, "is_required": 0},
        ],
    }],
    "Device": [{
        "name": "DEVICE-001", "device_id": "TH-001", "device_profile": "PROFILE-001",
        "site": "SITE-001", "zone": "ZONE-001", "status": "Active", "connectivity_type": "WiFi",
        "mac": "00:11:22:33:44:55", "config_version": 1, "online_state": "Offline", "last_seen": None,
    }],
    "Rule Configuration": [{
        "name": "RULE-001", "rule_id": "TEMP-HIGH", "version": 1, "rule_type": "THRESHOLD",
        "scope_type": "Device", "scope_ref": "DEVICE-001", "data_point": "temperature",
        "operator": ">", "threshold": 30, "duration_s": 30, "hysteresis": 1,
        "clear_duration_s": 10, "quiet_window_s": 600, "severity": "High",
        "event_type": "TEMPERATURE_HIGH", "schedule": '{"timezone": "Asia/Ho_Chi_Minh"}',
        "auto_close": 1, "active": 1,
    }],
    "Alarm": [],
}


class MockFrappe:
    def __init__(self):
        self.docs, self.creates = deepcopy(MOCK_DOCS), 0

    def close(self):
        pass

    def all(self, doctype: str, filters: list | None = None) -> list[dict]:
        records = self.docs.get(doctype, [])
        for field, operator, value in filters or []:
            if operator != "=":
                raise ValueError(f"Mock only supports '=' filters, got {operator}")
            records = [record for record in records if record.get(field) == value]
        return deepcopy(records)

    def find(self, doctype: str, field: str, value: str, filters: list | None = None) -> dict | None:
        records = [row for row in self.docs.get(doctype, []) if row.get(field) == value]
        for filter_field, operator, filter_value in filters or []:
            if operator != "=":
                raise ValueError(f"Mock only supports '=' filters, got {operator}")
            records = [record for record in records if record.get(filter_field) == filter_value]
        if len(records) > 1:
            raise ValueError(f"Ambiguous {doctype}.{field}={value}")
        return deepcopy(records[0]) if records else None

    def create(self, doctype: str, body: dict) -> dict:
        self.creates += 1
        record = {
            "name": f"MOCK-{doctype.upper().replace(' ', '-')}-{self.creates}",
            "modified": datetime.now(timezone.utc).isoformat(), **deepcopy(body),
        }
        self.docs.setdefault(doctype, []).append(record)
        return deepcopy(record)

    def update(self, doctype: str, name: str, body: dict) -> dict:
        for record in self.docs.get(doctype, []):
            if record["name"] == name:
                record.update(deepcopy(body))
                record["modified"] = datetime.now(timezone.utc).isoformat()
                return deepcopy(record)
        raise LookupError(f"Unknown {doctype}/{name}")


class MockDataPlane:
    def __init__(self):
        self.sent: list[tuple[str, str, dict]] = []

    def close(self):
        pass

    def send(self, kind: str, resource_key: str, payload: dict) -> dict:
        self.sent.append((kind, resource_key, deepcopy(payload)))
        version = payload.get("config_version") if kind == "device" else payload.get("version")
        return {"accepted": True, "applied_version": version}

    def latest(self, device_id: str) -> dict:
        return {"device_id": device_id, "temperature": 28.5, "humidity": 55, "mock": True}

    def telemetry(self, device_id: str) -> dict:
        return {"device_id": device_id, "data": [], "mock": True}
