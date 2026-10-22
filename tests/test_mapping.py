from app.adapters import MOCK_DOCS
from app.mapping import plans


def test_mapping_is_deterministic():
    first = plans(MOCK_DOCS, "demo", "Demo Organization")
    second = plans(MOCK_DOCS, "demo", "Demo Organization")
    assert first == second
    assert [item[2] for item in first] == [0, 10, 20, 30, 40, 50]
