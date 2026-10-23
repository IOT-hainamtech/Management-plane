import os
import json
from urllib.parse import quote, urlsplit

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv(
    "NEXTGRP_URL", "http://127.0.0.1:8080"
).rstrip("/")

if urlsplit(BASE_URL).hostname not in (
    "localhost", "127.0.0.1"
):
    raise RuntimeError("Only Frappe Local is allowed")

ORG_CODE = os.getenv("ORG_CODE", "demo")

HEADERS = {
    "Authorization": (
        f"token {os.environ['NEXTGRP_API_KEY']}:"
        f"{os.environ['NEXTGRP_API_SECRET']}"
    )
}


def get_document(client, doctype, name):
    url = (
        f"/api/resource/{quote(doctype, safe='')}/"
        f"{quote(name, safe='')}"
    )
    response = client.get(url)
    response.raise_for_status()
    return response.json()["data"]


with httpx.Client(
    base_url=BASE_URL,
    headers=HEADERS,
    timeout=15
) as client:
    site = get_document(client, "Site", "demo-site")
    zone = get_document(client, "Zone", "office")

if site["organization"] != ORG_CODE:
    raise ValueError("Site belongs to another Organization")

if zone["site"] != site["name"]:
    raise ValueError("Zone does not belong to Site")

site_payload = {
    "org_code": ORG_CODE,
    "site_code": site["site_code"],
    "name": site["site_name"],
    "version": int(site["version"])
}

zone_payload = {
    "org_code": ORG_CODE,
    "site_code": site["site_code"],
    "zone_code": zone["zone_code"],
    "name": zone["zone_name"],
    "version": int(zone["version"])
}

print("SITE:")
print(json.dumps(site_payload, indent=2, ensure_ascii=False))

print("\nZONE:")
print(json.dumps(zone_payload, indent=2, ensure_ascii=False))

print("\nFrappe Site/Zone: OK")