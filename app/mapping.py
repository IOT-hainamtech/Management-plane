import json
from dataclasses import dataclass


def need(doc: dict, field: str):
    value = doc.get(field)
    if value is None or value == "":
        raise ValueError(f"{doc.get('name', '?')}: missing {field}")
    return value


def version(doc: dict, field: str = "version") -> int:
    value = need(doc, field)
    if isinstance(value, bool):
        raise ValueError(f"Invalid {field}: {value}")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field}: {value}") from exc
    if result < 1 or str(result) != str(value).strip():
        raise ValueError(f"Invalid {field}: {value}")
    return result


def indexed(rows: list[dict]) -> dict[str, dict]:
    output: dict[str, dict] = {}
    for row in rows:
        key = str(need(row, "name"))
        if key in output:
            raise ValueError(f"Duplicate name: {key}")
        output[key] = row
    return output


def linked(doc: dict, field: str, documents: dict[str, dict], code_field: str) -> str:
    ref = str(need(doc, field))
    target = documents.get(ref)
    if target is None:
        raise ValueError(f"{doc.get('name', '?')}: unresolved link {field}={ref}")
    return str(need(target, code_field))


def normalized_dtype(value: object) -> str:
    types = {"float": "number", "int": "integer", "integer": "integer", "number": "number", "bool": "boolean", "boolean": "boolean", "string": "string", "text": "string"}
    result = types.get(str(value).strip().lower())
    if not result:
        raise ValueError(f"Unsupported data point type: {value}")
    return result


def optional_json(value: object, field: str):
    """Decode JSON stored by Frappe in a Small Text field without guessing."""
    if value is None or value == "":
        return None
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field} must contain JSON text")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} contains invalid JSON") from exc


def as_bool(value: object) -> bool:
    if isinstance(value, str):
        value = value.strip()
    try:
        return bool(int(value or 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid boolean flag: {value!r}") from exc


@dataclass(frozen=True)
class Context:
    sites: dict[str, dict]
    zones: dict[str, dict]
    profiles: dict[str, dict]
    devices: dict[str, dict]


def profile(doc: dict, org_code: str) -> dict:
    points = []
    for row in doc.get("profile_data_points", []):
        point = {
            "code": str(need(row, "code")), "label": str(need(row, "label")),
            "data_type": normalized_dtype(need(row, "data_type")),
            "channel": row.get("channel") or "telemetry", "unit": row.get("unit"),
            "min": row.get("min"), "max": row.get("max"),
            "precision": row.get("decimal_places"), "required": as_bool(row.get("is_required")),
        }
        allowed_values = optional_json(row.get("allowed_values"), "allowed_values")
        if allowed_values is not None:
            point["allowed_values"] = allowed_values
        points.append(point)

    event_types = [
        {"code": str(need(row, "event_code")), "label": str(need(row, "event_name"))}
        for row in doc.get("event_types", [])
    ]
    return {
        "org_code": org_code, "profile_code": str(need(doc, "profile_code")), "version": version(doc),
        "signal_model": str(need(doc, "signal_model")), "protocol": str(need(doc, "protocol")),
        "expected_interval_s": doc.get("expected_interval_s"), "heartbeat_interval_s": doc.get("heartbeat_interval_s"),
        "offline_after_missed": doc.get("offline_after_missed"), "event_types": event_types,
        "data_points": points,
    }


def device(doc: dict, context: Context, org_code: str) -> dict:
    status = str(need(doc, "status")).strip().lower().replace(" ", "_")
    if status not in {"registered", "active", "maintenance", "retired"}:
        raise ValueError(f"{doc.get('name', '?')}: unsupported device status {doc.get('status')!r}")
    return {
        "org_code": org_code, "device_id": str(need(doc, "device_id")),
        "profile_code": linked(doc, "device_profile", context.profiles, "profile_code"),
        "site_code": linked(doc, "site", context.sites, "site_code"),
        "zone_code": linked(doc, "zone", context.zones, "zone_code") if doc.get("zone") else None,
        "connectivity_type": doc.get("connectivity_type"), "mac": doc.get("mac"),
        "config_version": version(doc, "config_version"),
    }


def rule(doc: dict, context: Context, org_code: str) -> dict:
    scope_type = str(need(doc, "scope_type"))
    scope_map = {
        "Device": ("scope_ref", context.devices, "device_id", "DEVICE"),
        "Device Profile": ("scope_profile", context.profiles, "profile_code", "PROFILE"),
        "Zone": ("scope_zone", context.zones, "zone_code", "ZONE"),
        "Site": ("scope_site", context.sites, "site_code", "SITE"),
    }
    if scope_type not in scope_map:
        raise ValueError(f"{doc.get('name', '?')}: unsupported scope {scope_type!r}")
    field, records, code, target = scope_map[scope_type]
    return {
        "org_code": org_code, "rule_id": str(need(doc, "rule_id")), "version": version(doc),
        # THRESHOLD and STATE_CHANGE are contract enum values: preserve them verbatim.
        "rule_type": str(need(doc, "rule_type")), "scope": {"type": target, "ref": linked(doc, field, records, code)},
        "data_point": doc.get("data_point"), "operator": doc.get("operator"), "threshold": doc.get("threshold"),
        "duration_s": doc.get("duration_s"), "hysteresis": doc.get("hysteresis"),
        "clear_duration_s": doc.get("clear_duration_s"), "quiet_window_s": doc.get("quiet_window_s"),
        "severity": doc.get("severity"), "event_type": doc.get("event_type"),
        "open_limit_s": doc.get("open_limit_s"), "schedule": optional_json(doc.get("schedule"), "schedule"),
        "auto_close": as_bool(doc.get("auto_close")), "active": as_bool(doc.get("active")),
    }


def plans(docs: dict[str, list[dict]], org_code: str, org_name: str) -> list[tuple[str, str, int, int, dict]]:
    sites, zones = indexed(docs.get("Site", [])), indexed(docs.get("Zone", []))
    profiles, devices = indexed(docs.get("Device Profile", [])), indexed(docs.get("Device", []))
    context = Context(sites=sites, zones=zones, profiles=profiles, devices=devices)
    output: list[tuple[str, str, int, int, dict]] = []
    organization = {"org_code": org_code, "org_name": org_name, "version": 1}
    output.append(("organization", org_code, 0, 1, organization))
    for row in sites.values():
        payload = {"org_code": org_code, "site_code": str(need(row, "site_code")), "site_name": str(need(row, "site_name")), "version": version(row)}
        output.append(("site", payload["site_code"], 10, payload["version"], payload))
    for row in zones.values():
        payload = {"org_code": org_code, "zone_code": str(need(row, "zone_code")), "zone_name": str(need(row, "zone_name")), "site_code": linked(row, "site", sites, "site_code"), "version": version(row)}
        output.append(("zone", payload["zone_code"], 20, payload["version"], payload))
    for row in profiles.values():
        payload = profile(row, org_code)
        output.append(("profile", payload["profile_code"], 30, payload["version"], payload))
    for row in devices.values():
        payload = device(row, context, org_code)
        output.append(("device", payload["device_id"], 40, payload["config_version"], payload))
    for row in indexed(docs.get("Rule Configuration", [])).values():
        payload = rule(row, context, org_code)
        output.append(("rule", payload["rule_id"], 50, payload["version"], payload))
    return output
