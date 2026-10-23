import json
from types import SimpleNamespace

import httpx
import pytest

from app.application.errors import IntegrationError, is_permanent_error
from app.infrastructure.clients.data_plane import DataPlaneClient


@pytest.mark.parametrize("status, permanent", [(400, True), (503, False)])
def test_data_plane_http_error_is_classified_at_adapter_boundary(status, permanent):
    cfg = SimpleNamespace(data_plane_url="https://plane.example", data_plane_token="", org_code="demo")
    client = DataPlaneClient(cfg, transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    try:
        with pytest.raises(IntegrationError) as error:
            client.send("organization", "demo", {"org_code": "demo", "version": 1})
        assert is_permanent_error(error.value) is permanent
    finally:
        client.close()


def test_profile_org_code_is_sent_in_header_not_body():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "applied", "applied_version": 1})

    cfg = SimpleNamespace(data_plane_url="https://plane.example", data_plane_token="test-token", org_code="demo")
    client = DataPlaneClient(cfg, transport=httpx.MockTransport(respond))
    try:
        client.send("profile", "DEMO-PROF-VIB", {"org_code": "demo", "profile_code": "DEMO-PROF-VIB", "version": 1})
        assert requests[0].headers["X-Org-Code"] == "demo"
        assert json.loads(requests[0].content) == {"profile_code": "DEMO-PROF-VIB", "version": 1}
    finally:
        client.close()
