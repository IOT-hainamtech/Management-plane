from contextlib import contextmanager
from types import SimpleNamespace

from app.infrastructure.database import Store


class FakeSession:
    def __init__(self):
        self.added = []
        self.queries = 0

    def execute(self, _statement):
        self.queries += 1
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    def add(self, row):
        self.added.append(row)


class FakeSessions:
    def __init__(self):
        self.session = FakeSession()

    @contextmanager
    def begin(self):
        yield self.session


def test_stage_many_prefetches_batch_and_isolates_version_error():
    sessions = FakeSessions()
    store = SimpleNamespace(sessions=sessions)
    resources = [
        ("device", "TH-001", 40, 2, {"device_id": "TH-001", "status": "active"}, []),
        ("device", "TH-001", 40, 1, {"device_id": "TH-001", "status": "active"}, []),
        ("device", "TH-002", 40, 1, {"device_id": "TH-002", "status": "active"}, []),
    ]

    result = Store.stage_many(store, resources, batch_size=100)

    assert sessions.session.queries == 1
    assert [(row.kind, row.resource_key) for row in sessions.session.added] == [
        ("device", "TH-001"), ("device", "TH-002"),
    ]
    assert result["staged"] == 2
    assert result["invalid"] == [{
        "kind": "device", "key": "TH-001",
        "error": "device/TH-001: version decreased (1 < 2)",
    }]
