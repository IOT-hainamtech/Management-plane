from copy import deepcopy

from app.adapters import MOCK_DOCS
from app.mapping import plans


def test_mapping_is_deterministic():
    first = plans(MOCK_DOCS, "demo", "Demo Organization")
    second = plans(MOCK_DOCS, "demo", "Demo Organization")
    assert first == second
    assert [item[2] for item in first] == [0, 10, 20, 30, 40, 50]


def test_device_zone_is_optional():
    docs = deepcopy(MOCK_DOCS)
    docs["Device"][0]["zone"] = None
    result = plans(docs, "demo", "Demo Organization")
    assert result[4][4]["zone_code"] is None
