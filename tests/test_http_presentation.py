import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import httpx

from app.application.bridge import Bridge
from app.infrastructure.clients.mock import MockDataPlane, MockFrappe
from app.presentation.http import create_app


def test_http_layer_receives_injected_bridge_factory():
    cfg = SimpleNamespace(org_code="demo", nextgrp_mode="mock", data_plane_mode="mock")
    bridge = SimpleNamespace(store=SimpleNamespace(health=lambda: None))

    @contextmanager
    def bridge_factory(received_cfg):
        assert received_cfg is cfg
        yield bridge

    app = create_app(cfg, bridge_factory)

    async def check_lifecycle():
        async with app.router.lifespan_context(app):
            assert app.state.bridge is bridge
            assert app.state.store is bridge.store

    asyncio.run(check_lifecycle())
    assert any(route.path == "/internal/events/status" for route in app.routes)


def test_alarm_and_status_return_202_with_stable_ack_after_acceptance():
    class InboxStore:
        def __init__(self):
            self.events = {}

        def accept_event(self, event_id, kind, payload):
            if event_id in self.events:
                return self.events[event_id], True
            ack_id = f"ack-{len(self.events) + 1}"
            self.events[event_id] = ack_id
            return ack_id, False

    cfg = SimpleNamespace(
        org_code="demo", inbound_token="inbound-secret", admin_token="admin-secret",
        nextgrp_mode="mock", data_plane_mode="mock",
    )
    store = InboxStore()

    @contextmanager
    def bridge_factory(_):
        yield Bridge(cfg, store, MockFrappe(), MockDataPlane())

    app = create_app(cfg, bridge_factory)
    status = {"event_id": "status-1", "org_code": "demo", "device_id": "TH-001", "state": "online", "since": "2026-10-22T03:00:00Z"}
    alarm = {
        "event_id": "alarm-1", "org_code": "demo", "alarm_key": "TEMP-HIGH:TH-001:1",
        "episode_key": "TEMP-HIGH:TH-001:1", "kind": "occurrence", "rule_id": "TEMP-HIGH",
        "rule_version": 1, "device_id": "TH-001", "episode_start": "2026-10-22T03:00:00Z",
        "raised_at": "2026-10-22T03:01:00Z", "occurrence_seq": 1,
        "event": {"event_id": "sensor-1", "event_type": "TEMPERATURE_HIGH", "ts": "2026-10-22T03:01:00Z"},
    }

    async def check_routes():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for path, body in (("status", status), ("alarm", alarm)):
                    headers = {"Authorization": "Bearer inbound-secret", "Idempotency-Key": body["event_id"]}
                    first = await client.post(f"/internal/events/{path}", json=body, headers=headers)
                    repeated = await client.post(f"/internal/events/{path}", json=body, headers=headers)
                    assert first.status_code == repeated.status_code == 202
                    assert first.json()["accepted"] is True
                    assert first.json()["duplicate"] is False
                    assert repeated.json()["duplicate"] is True
                    assert repeated.json()["ack_id"] == first.json()["ack_id"]
                rejected = await client.post("/internal/events/status", json=status, headers={"Idempotency-Key": "status-1"})
                assert rejected.status_code == 401

    asyncio.run(check_routes())
