from copy import deepcopy


MOCK = {
    "Site": [
        {
            "name": "SITE-DOC-001",
            "site_code": "hq",
            "site_name": "Head Office"
        }
    ],

    "Zone": [
        {
            "name": "ZONE-DOC-001",
            "zone_code": "server-room",
            "site": "SITE-DOC-001"
        }
    ],

    "Device Profile": [
        {
            "name": "PROF-DOC-001",
            "profile_code": "TEMP_HUM_SENSOR",
            "version": 1,
            "signal_model": "TELEMETRY",
            "protocol": "MQTT",
            "expected_interval_s": 60,
            "heartbeat_interval_s": 60,
            "offline_after_missed": 3,
            "profile_data_points": [
                {
                    "code": "temperature",
                    "label": "Temperature",
                    "data_type": "Float",
                    "unit": "C",
                    "min": -30,
                    "max": 80,
                    "precision": 1
                },
                {
                    "code": "humidity",
                    "label": "Humidity",
                    "data_type": "Float",
                    "unit": "%",
                    "min": 0,
                    "max": 100,
                    "precision": 1
                }
            ]
        }
    ],

    "Device": [
        {
            "name": "DEVICE-DOC-001",
            "device_id": "TH-001",
            "device_profile": "PROF-DOC-001",
            "site": "SITE-DOC-001",
            "zone": "ZONE-DOC-001",
            "status": "Active",
            "connectivity_type": "WiFi",
            "mac": "00:11:22:33:44:55",
            "config_version": 1,
            "online_state": "Offline",
            "last_seen": None
        }
    ],

    "Rule Configuration": [
        {
            "name": "RULE-DOC-001",
            "rule_id": "TEMP-HIGH",
            "version": 1,
            "rule_type": "threshold",
            "scope_type": "Device",
            "scope_ref": "DEVICE-DOC-001",
            "data_point": "temperature",
            "operator": ">",
            "threshold": 30,
            "duration_s": 30,
            "hysteresis": 1,
            "clear_duration_s": 10,
            "quiet_window_s": 60,
            "severity": "Cao",
            "active": 1
        }
    ],

    "Alarm": []
}


class MockFrappe:

    def __init__(self):
        self.data = deepcopy(MOCK)
        self.creates = 0

    async def close(self):
        pass

    async def check(self):
        return "mock.integration"

    async def all(self, doctype):
        return deepcopy(
            self.data.get(doctype, [])
        )

    async def get(self, doctype, name):
        for row in self.data.get(doctype, []):
            if row["name"] == name:
                return deepcopy(row)

        raise ValueError(
            f"Not found: {doctype}/{name}"
        )

    async def find(
        self, doctype, field, value
    ):
        result = [
            row
            for row in self.data.get(doctype, [])
            if row.get(field) == value
        ]

        if len(result) > 1:
            raise ValueError(
                f"Duplicate {doctype}.{field}: {value}"
            )

        return (
            deepcopy(result[0])
            if result else None
        )

    async def create(self, doctype, data):
        self.creates += 1

        doc = {
            "name": (
                f"MOCK-{doctype.upper().replace(' ', '-')}"
                f"-{self.creates}"
            ),
            **deepcopy(data)
        }

        self.data.setdefault(
            doctype, []
        ).append(doc)

        return deepcopy(doc)

    async def update(
        self, doctype, name, data
    ):
        for row in self.data.get(doctype, []):
            if row["name"] == name:
                row.update(deepcopy(data))
                return deepcopy(row)

        raise ValueError(
            f"Not found: {doctype}/{name}"
        )


class MockDataPlane:

    def __init__(self):
        self.sent = []

    async def close(self):
        pass

    async def put_profile(self, code, body):
        self.sent.append(
            ("profile", code, deepcopy(body))
        )
        return {"ok": True}

    async def put_device(self, code, body):
        self.sent.append(
            ("device", code, deepcopy(body))
        )
        return {"ok": True}

    async def put_rule(self, code, body):
        self.sent.append(
            ("rule", code, deepcopy(body))
        )
        return {"ok": True}

    async def alarm_state(self, key, body):
        self.sent.append(
            ("alarm_state", key, deepcopy(body))
        )
        return {"ok": True}

    async def latest(self, device_id):
        return {
            "device_id": device_id,
            "temperature": 28.5,
            "humidity": 55,
            "mock": True
        }

    async def telemetry(self, device_id):
        return {
            "device_id": device_id,
            "data": [],
            "mock": True
        }

    async def zone_summary(self, zone):
        return {
            "zone": zone,
            "devices": 1,
            "mock": True
        }