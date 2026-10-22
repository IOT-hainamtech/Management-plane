import json
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import quote

import httpx


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
        "name": "DEVICE-001", "device_id": "TH-001", "device_profile": "PROFILE-001", "site": "SITE-001", "zone": "ZONE-001",
        "status": "Active", "connectivity_type": "WiFi", "mac": "00:11:22:33:44:55", "config_version": 1,
        "online_state": "Offline", "last_seen": None,
    }],
    "Rule Configuration": [{
        "name": "RULE-001", "rule_id": "TEMP-HIGH", "version": 1, "rule_type": "THRESHOLD", "scope_type": "Device", "scope_ref": "DEVICE-001",
        "data_point": "temperature", "operator": ">", "threshold": 30, "duration_s": 30, "hysteresis": 1,
        "clear_duration_s": 10, "quiet_window_s": 600, "severity": "High", "event_type": "TEMPERATURE_HIGH", "schedule": "{\"timezone\": \"Asia/Ho_Chi_Minh\"}", "auto_close": 1, "active": 1,
    }],
    "Alarm": [],
}


class FrappeClient:
    def __init__(self, cfg):
        self.client = httpx.Client(
            base_url=cfg.nextgrp_url.rstrip("/"),
            headers={"Authorization": f"token {cfg.nextgrp_api_key}:{cfg.nextgrp_api_secret}", "Accept": "application/json"},
            timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False,
        )

    @staticmethod
    def path(doctype: str, name: str | None = None) -> str:
        path = "/api/resource/" + quote(doctype, safe="")
        return path if name is None else path + "/" + quote(name, safe="")

    def close(self):
        self.client.close()

    def request(self, method: str, path: str, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def all(self, doctype: str) -> list[dict]:
        names, offset = [], 0
        while True:
            page = self.request("GET", self.path(doctype), params={"fields": '["name"]', "limit_start": offset, "limit_page_length": 100})["data"]
            names.extend(page)
            if len(page) < 100:
                break
            offset += 100
        return [self.get(doctype, item["name"]) for item in names]

    def get(self, doctype: str, name: str) -> dict:
        return self.request("GET", self.path(doctype, name))["data"]

    def find(self, doctype: str, field: str, value: str) -> dict | None:
        page = self.request("GET", self.path(doctype), params={"fields": '["name"]', "filters": json.dumps([[field, "=", value]], ensure_ascii=False), "limit_page_length": 2})["data"]
        if len(page) > 1:
            raise ValueError(f"Ambiguous {doctype}.{field}={value}")
        return self.get(doctype, page[0]["name"]) if page else None

    def create(self, doctype: str, body: dict) -> dict:
        return self.request("POST", self.path(doctype), json=body)["data"]

    def update(self, doctype: str, name: str, body: dict) -> dict:
        return self.request("PUT", self.path(doctype, name), json=body)["data"]


class DataPlaneClient:
    def __init__(self, cfg):
        headers = {"Accept": "application/json"}
        if cfg.data_plane_token:
            headers["Authorization"] = f"Bearer {cfg.data_plane_token}"
        self.client = httpx.Client(base_url=cfg.data_plane_url.rstrip("/"), headers=headers, timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False)

    def close(self):
        self.client.close()

    def send(self, kind: str, resource_key: str, payload: dict) -> dict:
        # IF-00: organization/site/zone; IF-01: device; IF-02: profile;
        # IF-03: rule; IF-06: alarm state.
        routes = {
            "organization": "/api/v1/sync/organizations/", "site": "/api/v1/sync/sites/", "zone": "/api/v1/sync/zones/",
            "profile": "/api/v1/sync/profiles/", "device": "/api/v1/sync/devices/", "rule": "/api/v1/sync/rules/",
        }
        if kind == "alarm_state":
            method, path = "POST", "/api/v1/alarms/" + quote(resource_key, safe="") + "/state"
        elif kind in routes:
            method, path = "PUT", routes[kind] + quote(resource_key, safe="")
        else:
            raise ValueError(f"Unsupported data plane sync kind: {kind}")
        response = self.client.request(method, path, json=payload)
        response.raise_for_status()
        if not response.content:
            raise ValueError(f"{kind} response must contain JSON")
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise ValueError(f"{kind} response is not JSON") from exc
        if not isinstance(body, dict):
            raise ValueError(f"{kind} response must be a JSON object")
        if body.get("accepted") is False:
            raise ValueError(f"Data Plane rejected {kind}/{resource_key}")
        if kind != "alarm_state":
            applied_version = body.get("applied_version")
            if isinstance(applied_version, bool) or not isinstance(applied_version, int):
                raise ValueError(f"{kind} response missing integer applied_version")
        return body

    def latest(self, device_id: str) -> dict:
        return self._get("/api/v1/devices/" + quote(device_id, safe="") + "/latest")

    def telemetry(self, device_id: str) -> dict:
        return self._get("/api/v1/devices/" + quote(device_id, safe="") + "/telemetry")

    def _get(self, path: str) -> dict:
        response = self.client.get(path)
        response.raise_for_status()
        return response.json()


class MockFrappe:
    def __init__(self):
        self.docs, self.creates = deepcopy(MOCK_DOCS), 0

    def close(self):
        pass

    def all(self, doctype: str) -> list[dict]:
        return deepcopy(self.docs.get(doctype, []))

    def find(self, doctype: str, field: str, value: str) -> dict | None:
        records = [row for row in self.docs.get(doctype, []) if row.get(field) == value]
        if len(records) > 1:
            raise ValueError(f"Ambiguous {doctype}.{field}={value}")
        return deepcopy(records[0]) if records else None

    def create(self, doctype: str, body: dict) -> dict:
        self.creates += 1
        record = {"name": f"MOCK-{doctype.upper().replace(' ', '-')}-{self.creates}", "modified": datetime.now(timezone.utc).isoformat(), **deepcopy(body)}
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
        return {"accepted": True, "applied_version": payload.get("version")}

    def latest(self, device_id: str) -> dict:
        return {"device_id": device_id, "temperature": 28.5, "humidity": 55, "mock": True}

    def telemetry(self, device_id: str) -> dict:
        return {"device_id": device_id, "data": [], "mock": True}
