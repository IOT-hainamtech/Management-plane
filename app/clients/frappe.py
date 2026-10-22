import json
from urllib.parse import quote

import httpx


class FrappeClient:

    def __init__(
        self,
        base_url: str,
        key: str,
        secret: str
    ):
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"token {key}:{secret}",
                "Accept": "application/json"
            },
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False
        )

    @staticmethod
    def _path(
        doctype: str,
        name: str | None = None
    ) -> str:

        path = (
            "/api/resource/"
            + quote(doctype, safe="")
        )

        if name is None:
            return path

        return path + "/" + quote(name, safe="")

    async def close(self):
        await self.client.aclose()

    async def _json(
        self,
        method: str,
        url: str,
        **kwargs
    ):
        response = await self.client.request(
            method, url, **kwargs
        )

        response.raise_for_status()
        return response.json()

    async def check(self):
        result = await self._json(
            "GET",
            "/api/method/frappe.auth.get_logged_user"
        )

        return result["message"]

    async def get(
        self,
        doctype: str,
        name: str
    ):
        result = await self._json(
            "GET",
            self._path(doctype, name)
        )

        return result["data"]

    async def list(
        self,
        doctype: str,
        filters: list | None = None
    ):
        results = []
        offset = 0

        while True:
            params = {
                "fields": '["name"]',
                "limit_start": offset,
                "limit_page_length": 100
            }

            if filters is not None:
                params["filters"] = json.dumps(
                    filters,
                    ensure_ascii=False
                )

            result = await self._json(
                "GET",
                self._path(doctype),
                params=params
            )

            page = result["data"]
            results.extend(page)

            if len(page) < 100:
                break

            offset += 100

        return results

    async def all(self, doctype: str):
        items = await self.list(doctype)

        return [
            await self.get(doctype, item["name"])
            for item in items
        ]

    async def find(
        self,
        doctype: str,
        field: str,
        value: str
    ):
        items = await self.list(
            doctype,
            [[field, "=", value]]
        )

        if len(items) > 1:
            raise ValueError(
                f"Ambiguous {doctype}.{field}={value}"
            )

        if not items:
            return None

        return await self.get(
            doctype,
            items[0]["name"]
        )

    async def create(
        self,
        doctype: str,
        data: dict
    ):
        result = await self._json(
            "POST",
            self._path(doctype),
            json=data
        )

        return result["data"]

    async def update(
        self,
        doctype: str,
        name: str,
        data: dict
    ):
        result = await self._json(
            "PUT",
            self._path(doctype, name),
            json=data
        )

        return result["data"]