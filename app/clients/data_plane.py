
from urllib.parse import quote

import httpx


class DataPlaneClient:

    def __init__(
        self,
        base_url: str,
        token: str
    ):
        headers = {
            "Accept": "application/json"
        }

        if token:
            headers["Authorization"] = (
                f"Bearer {token}"
            )

        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(
                20.0,
                connect=5.0
            ),
            follow_redirects=False
        )

    async def close(self):
        await self.client.aclose()

    async def _send(
        self,
        method: str,
        path: str,
        payload: dict | None = None
    ):
        response = await self.client.request(
            method,
            path,
            json=payload
        )

        response.raise_for_status()

        if response.content:
            return response.json()

        return {"ok": True}

    async def put_profile(
        self,
        code: str,
        body: dict
    ):
        return await self._send(
            "PUT",
            "/api/v1/sync/profiles/"
            + quote(code, safe=""),
            body
        )

    async def put_device(
        self,
        code: str,
        body: dict
    ):
        return await self._send(
            "PUT",
            "/api/v1/sync/devices/"
            + quote(code, safe=""),
            body
        )

    async def put_rule(
        self,
        code: str,
        body: dict
    ):
        return await self._send(
            "PUT",
            "/api/v1/sync/rules/"
            + quote(code, safe=""),
            body
        )

    async def alarm_state(
        self,
        alarm_key: str,
        body: dict
    ):
        return await self._send(
            "POST",
            "/api/v1/alarms/"
            + quote(alarm_key, safe="")
            + "/state",
            body
        )

    async def latest(
        self,
        device_id: str
    ):
        return await self._send(
            "GET",
            "/api/v1/devices/"
            + quote(device_id, safe="")
            + "/latest"
        )

    async def telemetry(
        self,
        device_id: str
    ):
        return await self._send(
            "GET",
            "/api/v1/devices/"
            + quote(device_id, safe="")
            + "/telemetry"
        )

    async def zone_summary(
        self,
        zone_code: str
    ):
        return await self._send(
            "GET",
            "/api/v1/zones/"
            + quote(zone_code, safe="")
            + "/summary"
        )