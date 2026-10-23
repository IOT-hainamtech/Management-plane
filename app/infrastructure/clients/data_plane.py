import json
from urllib.parse import quote

import httpx

from app.application.errors import IntegrationError


class DataPlaneClient:
    def __init__(self, cfg, transport=None):
        headers = {"Accept": "application/json"}
        if cfg.data_plane_token:
            headers["Authorization"] = f"Bearer {cfg.data_plane_token}"
        self.client = httpx.Client(
            base_url=cfg.data_plane_url.rstrip("/"), headers=headers,
            timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def send(self, kind: str, resource_key: str, payload: dict) -> dict:
        encoded = quote(resource_key, safe="")
        if kind == "alarm_state":
            method, path = "POST", f"/api/v1/alarms/{encoded}/state"
        elif kind == "organization":
            method, path = "PUT", f"/api/v1/sync/organizations/{quote(payload['org_code'], safe='')}"
        elif kind == "site":
            method, path = "PUT", "/api/v1/sync/sites/{}/{}".format(
                quote(payload["org_code"], safe=""), quote(payload["site_code"], safe=""),
            )
        elif kind == "zone":
            method, path = "PUT", "/api/v1/sync/zones/{}/{}/{}".format(
                quote(payload["org_code"], safe=""), quote(payload["site_code"], safe=""),
                quote(payload["zone_code"], safe=""),
            )
        elif kind in {"profile", "device", "rule"}:
            collection = {"profile": "profiles", "device": "devices", "rule": "rules"}[kind]
            method, path = "PUT", f"/api/v1/sync/{collection}/{encoded}"
        else:
            raise ValueError(f"Unsupported Data Plane sync kind: {kind}")

        response = self._request(method, path, json=payload)
        if not response.content:
            raise ValueError(f"{kind} response must contain JSON")
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise ValueError(f"{kind} response is not JSON") from exc
        if not isinstance(body, dict):
            raise ValueError(f"{kind} response must be a JSON object")
        if body.get("accepted") is False:
            raise ValueError(f"Data Plane rejected {kind}/{resource_key}")
        if kind != "alarm_state":
            applied = body.get("applied_version")
            if isinstance(applied, bool) or not isinstance(applied, int):
                raise ValueError(f"{kind} response missing integer applied_version")
        return body

    def latest(self, device_id: str) -> dict:
        return self._get(f"/api/v1/devices/{quote(device_id, safe='')}/latest")

    def telemetry(self, device_id: str) -> dict:
        return self._get(f"/api/v1/devices/{quote(device_id, safe='')}/telemetry")

    def _get(self, path: str) -> dict:
        response = self._request("GET", path)
        return response.json()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            raise IntegrationError(
                f"Data Plane HTTP {exc.response.status_code}",
                permanent=exc.response.status_code in {400, 401, 403, 404, 409, 422},
            ) from exc
        except httpx.RequestError as exc:
            raise IntegrationError(f"Data Plane request failed: {exc}") from exc
