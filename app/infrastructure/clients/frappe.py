"""Synchronous Frappe REST adapter for the nextGRP Management Plane."""

import json
from copy import deepcopy
from urllib.parse import quote

import httpx

from app.application.errors import IntegrationError


class FrappeClient:
    def __init__(self, cfg, transport=None):
        self._profile_cache: dict[str, tuple[str, dict]] = {}
        self.client = httpx.Client(
            base_url=cfg.nextgrp_url.rstrip("/"),
            headers={
                "Authorization": f"token {cfg.nextgrp_api_key}:{cfg.nextgrp_api_secret}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
            transport=transport,
        )

    @staticmethod
    def path(doctype: str, name: str | None = None) -> str:
        path = "/api/resource/" + quote(doctype, safe="")
        return path if name is None else path + "/" + quote(name, safe="")

    def close(self):
        self.client.close()

    def request(self, method: str, path: str, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise IntegrationError(
                f"nextGRP HTTP {exc.response.status_code}",
                permanent=exc.response.status_code in {400, 401, 403, 404, 409, 422},
            ) from exc
        except httpx.RequestError as exc:
            raise IntegrationError(f"nextGRP request failed: {exc}") from exc
        return response.json()

    def all(self, doctype: str, filters: list | None = None) -> list[dict]:
        records, offset = [], 0
        # Child tables require the detail API. Other DocTypes are fetched in
        # pages, so N Devices do not produce N detail requests each cycle.
        detail_required = doctype == "Device Profile"
        while True:
            params = {
                "fields": '["name","modified"]' if detail_required else '["*"]',
                "limit_start": offset,
                "limit_page_length": 100,
                "order_by": "name asc",
            }
            if filters:
                params["filters"] = json.dumps(filters, ensure_ascii=False)
            page = self.request("GET", self.path(doctype), params=params)["data"]
            records.extend(page)
            if len(page) < 100:
                break
            offset += 100
        if not detail_required:
            return records
        current = {}
        output = []
        for item in records:
            name, modified = item["name"], item.get("modified", "")
            cached = self._profile_cache.get(name)
            document = cached[1] if cached and cached[0] == modified else self.get(doctype, name)
            current[name] = (modified, document)
            output.append(deepcopy(document))
        self._profile_cache = current
        return output

    def get(self, doctype: str, name: str) -> dict:
        return self.request("GET", self.path(doctype, name))["data"]

    def find(self, doctype: str, field: str, value: str, filters: list | None = None) -> dict | None:
        criteria = [[field, "=", value], *(filters or [])]
        page = self.request(
            "GET", self.path(doctype),
            params={
                "fields": '["*"]',
                "filters": json.dumps(criteria, ensure_ascii=False),
                "limit_page_length": 2,
            },
        )["data"]
        if len(page) > 1:
            raise ValueError(f"Ambiguous {doctype}.{field}={value}")
        return page[0] if page else None

    def create(self, doctype: str, body: dict) -> dict:
        return self.request("POST", self.path(doctype), json=body)["data"]

    def update(self, doctype: str, name: str, body: dict) -> dict:
        return self.request("PUT", self.path(doctype, name), json=body)["data"]
