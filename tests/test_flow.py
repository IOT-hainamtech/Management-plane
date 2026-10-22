from copy import deepcopy

import pytest

from app.adapters import MOCK_DOCS
from app.mapping import plans
from app.schemas import AlarmEvent


def test_order_and_links():
    result = plans(MOCK_DOCS, "demo", "Demo Organization")
    assert [item[0] for item in result] == ["organization", "site", "zone", "profile", "device", "rule"]
    device = result[4][4]
    assert device["profile_code"] == "TEMP_HUM_SENSOR"
    assert (device["org_code"], device["site_code"], device["zone_code"]) == ("demo", "demo-site", "office")
    assert result[3][4]["data_points"][0]["data_type"] == "number"
    assert result[5][4]["scope"] == {"type": "DEVICE", "ref": "TH-001"}


def test_missing_link_fails():
    docs = deepcopy(MOCK_DOCS)
    docs["Device"][0]["device_profile"] = "UNKNOWN-PROFILE"
    with pytest.raises(ValueError, match="unresolved link"):
        plans(docs, "demo", "Demo")


def test_schema_requires_timezone():
    base = {"event_id": "alarm-001", "org_code": "demo", "alarm_key": "TEMP-HIGH:TH-001:001", "kind": "occurrence", "rule_id": "TEMP-HIGH", "device_id": "TH-001"}
    with pytest.raises(Exception):
        AlarmEvent.model_validate({**base, "raised_at": "2026-10-22T10:00:00"})
    event = AlarmEvent.model_validate({**base, "raised_at": "2026-10-22T10:00:00Z"})
    assert event.kind == "occurrence"
