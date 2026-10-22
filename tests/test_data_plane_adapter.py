from types import SimpleNamespace

import httpx
import pytest

from app.application.errors import IntegrationError, is_permanent_error
from app.infrastructure.clients.data_plane import DataPlaneClient


@pytest.mark.parametrize("status, permanent", [(400, True), (503, False)])
def test_data_plane_http_error_is_classified_at_adapter_boundary(status, permanent):
    cfg = SimpleNamespace(data_plane_url="https://plane.example", data_plane_token="")
    client = DataPlaneClient(cfg, transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    try:
        with pytest.raises(IntegrationError) as error:
            client.send("organization", "demo", {"org_code": "demo", "version": 1})
        assert is_permanent_error(error.value) is permanent
    finally:
        client.close()
