from copy import deepcopy

import pytest

from app.infrastructure.clients import MOCK_DOCS
from app.domain.mapping import plans


def test_mapping_is_deterministic():
    first = plans(MOCK_DOCS, "demo", "Demo Organization")
    second = plans(MOCK_DOCS, "demo", "Demo Organization")
    assert first == second
    assert [item[2] for item in first] == [0, 10, 20, 30, 40, 50]


def test_organization_payload_uses_data_plane_name_field():
    organization = next(item for item in plans(MOCK_DOCS, "demo", "Demo Organization") if item[0] == "organization")
    assert organization[4] == {
        "org_code": "demo",
        "name": "Demo Organization",
        "version": 1,
    }


def test_site_payload_uses_data_plane_name_field():
    site = next(item for item in plans(MOCK_DOCS, "demo", "Demo Organization") if item[0] == "site")
    assert site[4] == {
        "org_code": "demo",
        "site_code": "demo-site",
        "name": "Demo Site",
        "version": 1,
    }


def test_zone_payload_uses_data_plane_name_field():
    zone = next(item for item in plans(MOCK_DOCS, "demo", "Demo Organization") if item[0] == "zone")
    assert zone[4] == {
        "org_code": "demo",
        "site_code": "demo-site",
        "zone_code": "office",
        "name": "Office",
        "version": 1,
    }


def test_device_requires_real_zone_for_data_plane_contract():
    docs = deepcopy(MOCK_DOCS)
    docs["Device"][0]["zone"] = None
    with pytest.raises(ValueError, match="missing zone"):
        plans(docs, "demo", "Demo Organization")


def test_device_transport_fields_match_data_plane_contract():
    device = next(item for item in plans(MOCK_DOCS, "demo", "Demo Organization") if item[0] == "device")
    assert device[4]["connectivity_type"] == "WIFI"
    assert "mac" not in device[4]


def test_hardware_model_with_only_data_points_maps_to_telemetry():
    docs = deepcopy(MOCK_DOCS)
    docs["Device Profile"][0]["signal_model"] = "MC-38"
    docs["Device Profile"][0]["event_types"] = []
    profile = next(item for item in plans(docs, "demo", "Demo Organization") if item[0] == "profile")
    assert profile[4]["signal_model"] == "TELEMETRY"


def test_ambiguous_hardware_model_without_signal_shape_fails():
    docs = deepcopy(MOCK_DOCS)
    docs["Device Profile"][0]["signal_model"] = "MC-38"
    docs["Device Profile"][0]["profile_data_points"] = []
    docs["Device Profile"][0]["event_types"] = []
    with pytest.raises(ValueError, match="Cannot infer signal_model"):
        plans(docs, "demo", "Demo Organization")
