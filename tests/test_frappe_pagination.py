from types import SimpleNamespace

import httpx
import pytest

from app.application.errors import IntegrationError
from app.infrastructure.clients.frappe import FrappeClient


def test_large_device_scan_uses_pages_and_profile_child_table_is_cached():
    calls = {"devices": 0, "profiles": 0, "profile_details": 0}
    devices = [{"name": f"D-{index:03}", "device_id": f"device-{index:03}"} for index in range(205)]

    def respond(request):
        if request.url.path == "/api/resource/Device":
            calls["devices"] += 1
            start = int(request.url.params["limit_start"])
            return httpx.Response(200, json={"data": devices[start:start + 100]})
        if request.url.path == "/api/resource/Device Profile":
            calls["profiles"] += 1
            return httpx.Response(200, json={"data": [{"name": "P-001", "modified": "2026-10-22 10:00:00"}]})
        if request.url.path == "/api/resource/Device Profile/P-001":
            calls["profile_details"] += 1
            return httpx.Response(200, json={"data": {"name": "P-001", "event_types": []}})
        return httpx.Response(404)

    cfg = SimpleNamespace(nextgrp_url="https://nextgrp.example", nextgrp_api_key="key", nextgrp_api_secret="secret")
    client = FrappeClient(cfg, transport=httpx.MockTransport(respond))
    try:
        assert len(client.all("Device")) == 205
        assert calls["devices"] == 3
        assert client.all("Device Profile") == client.all("Device Profile")
        assert calls["profiles"] == 2
        assert calls["profile_details"] == 1
    finally:
        client.close()


@pytest.mark.parametrize("status, permanent", [(400, True), (503, False)])
def test_frappe_http_failure_is_translated_at_adapter_boundary(status, permanent):
    cfg = SimpleNamespace(nextgrp_url="https://nextgrp.example", nextgrp_api_key="key", nextgrp_api_secret="secret")
    client = FrappeClient(cfg, transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    try:
        with pytest.raises(IntegrationError) as error:
            client.all("Device")
        assert error.value.permanent is permanent
    finally:
        client.close()
