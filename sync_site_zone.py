
import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

try:
    import httpx
    from dotenv import load_dotenv
except ImportError:
    print("Thieu thu vien. Chay: python -m pip install httpx python-dotenv", file=sys.stderr)
    sys.exit(1)


def required_env(name: str, alternative: str | None = None) -> str:
    value = (os.getenv(name) or (os.getenv(alternative) if alternative else None) or "").strip()
    if not value:
        names = name if not alternative else f"{name} (hoac {alternative})"
        raise ValueError(f"Thieu bien moi truong: {names}")
    return value


def normalized_url(raw: str, label: str, local_only: bool = False) -> str:
    value = raw.strip().rstrip("/")
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment
            or any(char.isspace() for char in value)):
        raise ValueError(f"{label} khong phai base URL hop le: {value!r}")
    if local_only and parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            "Bai test nay chi doc Frappe LOCAL (127.0.0.1/localhost); "
            "khong tu dong doc nextGRP production."
        )
    return value


def get_document(client: httpx.Client, doctype: str, name: str) -> dict:
    route = f"/api/resource/{quote(doctype, safe='')}/{quote(name, safe='')}"
    response = client.get(route)
    response.raise_for_status()
    data = response.json().get("data")
    if not isinstance(data, dict):
        raise ValueError(f"Frappe response cho {doctype}/{name} thieu 'data'")
    return data


def positive_version(data: dict, doctype: str) -> int:
    raw = data.get("version")
    if isinstance(raw, bool) or raw in (None, ""):
        raise ValueError(f"{doctype} chua co version hop le")
    try:
        version = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{doctype}.version khong phai so nguyen: {raw!r}") from exc
    if version < 1 or str(raw).strip() not in (str(version), f"{version}.0"):
        raise ValueError(f"{doctype}.version phai la so nguyen duong: {raw!r}")
    return version


def map_documents(site: dict, zone: dict, org: str, site_code: str, zone_code: str):
    if site.get("organization") != org:
        raise ValueError("Site khong thuoc Organization duoc cau hinh")
    if site.get("site_code") != site_code:
        raise ValueError("site_code trong Frappe khac ma Site duoc yeu cau")
    if zone.get("zone_code") != zone_code:
        raise ValueError("zone_code trong Frappe khac ma Zone duoc yeu cau")
    if zone.get("site") != site.get("name"):
        raise ValueError("Zone khong lien ket dung Site trong Frappe")
    if not isinstance(site.get("site_name"), str) or not site["site_name"].strip():
        raise ValueError("Site.site_name rong")
    if not isinstance(zone.get("zone_name"), str) or not zone["zone_name"].strip():
        raise ValueError("Zone.zone_name rong")

    site_payload = {
        "org_code": org,
        "site_code": site_code,
        "name": site["site_name"],
        "version": positive_version(site, "Site"),
    }
    zone_payload = {
        "org_code": org,
        "site_code": site_code,
        "zone_code": zone_code,
        "name": zone["zone_name"],
        "version": positive_version(zone, "Zone"),
    }
    return site_payload, zone_payload


def put_sync(client: httpx.Client, route: str, payload: dict, kind: str) -> None:
    response = client.put(route, json=payload)
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError as exc:
        raise ValueError(f"{kind} tra ve HTTP {response.status_code} nhung khong co JSON") from exc
    if not isinstance(body, dict):
        raise ValueError(f"{kind} tra ve JSON khong hop le")

    status = body.get("status")
    applied_version = body.get("applied_version")
    duplicate = body.get("duplicate")
    resource_id = body.get("resource_id")
    print(f"[{kind}] HTTP {response.status_code} | status={status} | "
          f"resource_id={resource_id} | version={applied_version} | duplicate={duplicate}")

    # The agreed contract responds with status=applied and the applied version.
    # A 2xx alone does not guarantee the Data Plane applied this version.
    if status != "applied":
        raise ValueError(f"{kind} khong xac nhan status=applied: {body}")
    if str(applied_version) != str(payload["version"]):
        raise ValueError(f"{kind} sai applied_version: {body}")
    expected_id = payload["site_code"] if kind == "SITE" else payload["zone_code"]
    if resource_id != expected_id:
        raise ValueError(f"{kind} sai resource_id: {body}")


def main() -> int:
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

    parser = argparse.ArgumentParser(description="Chi dong bo Site va Zone tu Frappe Local")
    parser.add_argument("--site-code", default="demo-site")
    parser.add_argument("--zone-code", default="office")
    parser.add_argument("--dry-run", action="store_true", help="Chi GET Frappe va in JSON; khong PUT")
    args = parser.parse_args()

    org = os.getenv("ORG_CODE", "demo").strip()
    if not org:
        raise ValueError("ORG_CODE khong duoc rong")
    frappe_url = normalized_url(
        required_env("NEXTGRP_URL"), "NEXTGRP_URL", local_only=True
    )
    api_key = required_env("NEXTGRP_API_KEY")
    api_secret = required_env("NEXTGRP_API_SECRET")

    with httpx.Client(
        base_url=frappe_url,
        headers={"Authorization": f"token {api_key}:{api_secret}"},
        timeout=15.0,
        follow_redirects=False,
        trust_env=False,  # Bypass WSL proxy for the LAN/local demo.
    ) as frappe:
        site = get_document(frappe, "Site", args.site_code)
        zone = get_document(frappe, "Zone", args.zone_code)

    site_payload, zone_payload = map_documents(site, zone, org, args.site_code, args.zone_code)
    print("[OK] Frappe Local: GET Site va Zone, kiem tra quan he thanh cong")
    print("SITE payload:\n" + json.dumps(site_payload, ensure_ascii=False, indent=2))
    print("ZONE payload:\n" + json.dumps(zone_payload, ensure_ascii=False, indent=2))

    if args.dry_run:
        print("[DRY RUN] Khong gui du lieu sang Data Plane")
        return 0

    dp_url = normalized_url(
        required_env("DP_BASE_URL", "DATA_PLANE_URL"), "DP_BASE_URL"
    )
    token = (os.getenv("DP_TOKEN") or "").strip()
    if not token:
        token = getpass.getpass("Nhap Data Plane Bearer token (khong hien ky tu): ").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise ValueError("Data Plane Bearer token khong duoc rong")
    if urlsplit(dp_url).scheme == "http":
        print("[CANH BAO] DP dung HTTP khong ma hoa. Chi dung tren LAN demo tin cay.")

    site_path = (f"/api/v1/sync/sites/{quote(org, safe='')}/"
                 f"{quote(args.site_code, safe='')}")
    zone_path = (f"/api/v1/sync/zones/{quote(org, safe='')}/"
                 f"{quote(args.site_code, safe='')}/{quote(args.zone_code, safe='')}")
    with httpx.Client(
        base_url=dp_url,
        headers={
            "X-Org-Code": org,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=15.0,
        follow_redirects=False,
        trust_env=False,
    ) as data_plane:
        # Critical order: create/update the parent Site before Zone.
        put_sync(data_plane, site_path, site_payload, "SITE")
        put_sync(data_plane, zone_path, zone_payload, "ZONE")

    print("[PASS] Da dong bo Site + Zone tu Frappe Local sang Data Plane")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except httpx.HTTPStatusError as exc:
        response = exc.response
        print(f"[FAIL] HTTP {response.status_code} tai {response.request.method} "
              f"{response.request.url.path}: {response.text[:800]}", file=sys.stderr)
        sys.exit(1)
    except (httpx.RequestError, ValueError, KeyError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        sys.exit(1)
