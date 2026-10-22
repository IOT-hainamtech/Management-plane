from dataclasses import dataclass


def required(doc: dict, field: str):
    value = doc.get(field)

    if value is None or value == "":
        raise ValueError(
            f"Missing required field {field} "
            f"in {doc.get('name', 'document')}"
        )

    return value


def revision(doc: dict, key: str) -> int:
    value = required(doc, key)

    try:
        number = int(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"{key} must be positive integer: {value!r}"
        ) from exc

    if number < 1 or str(number) != str(value).strip():
        raise ValueError(
            f"{key} must be positive integer: {value!r}"
        )

    return number


def index(docs: list[dict]) -> dict[str, dict]:
    return {
        required(doc, "name"): doc
        for doc in docs
    }


def link_code(
    indexed: dict[str, dict],
    doc: dict,
    link_field: str,
    code_field: str
) -> str:

    link = required(doc, link_field)
    target = indexed.get(link)

    if target is None:
        raise ValueError(
            f"Unresolved Link {link_field}={link!r} "
            f"in {doc.get('name')}"
        )

    return str(required(target, code_field))


@dataclass
class Context:
    profiles: dict[str, dict]
    devices: dict[str, dict]
    sites: dict[str, dict]
    zones: dict[str, dict]


def map_profile(doc: dict) -> dict:
    return {
        "profile_code": required(
            doc, "profile_code"
        ),
        "version": revision(
            doc, "version"
        ),
        "signal_model": required(
            doc, "signal_model"
        ),
        "protocol": required(
            doc, "protocol"
        ),
        "expected_interval_s": doc.get(
            "expected_interval_s"
        ),
        "heartbeat_interval_s": doc.get(
            "heartbeat_interval_s"
        ),
        "offline_after_missed": doc.get(
            "offline_after_missed"
        ),
        "data_points": [
            {
                key: row.get(key)
                for key in (
                    "code",
                    "label",
                    "data_type",
                    "unit",
                    "min",
                    "max",
                    "precision"
                )
            }
            for row in doc.get(
                "profile_data_points", []
            )
        ]
    }


def map_device(
    doc: dict,
    ctx: Context
) -> dict:

    status = (
        str(required(doc, "status"))
        .strip()
        .lower()
        .replace(" ", "_")
    )

    allowed = {
        "registered",
        "active",
        "maintenance",
        "retired"
    }

    if status not in allowed:
        raise ValueError(
            f"Device status not mapped: "
            f"{doc.get('status')!r}"
        )

    return {
        "device_id": required(
            doc, "device_id"
        ),
        "profile_code": link_code(
            ctx.profiles,
            doc,
            "device_profile",
            "profile_code"
        ),
        "site_code": link_code(
            ctx.sites,
            doc,
            "site",
            "site_code"
        ),
        "zone_code": link_code(
            ctx.zones,
            doc,
            "zone",
            "zone_code"
        ),
        "status": status,
        "connectivity_type": doc.get(
            "connectivity_type"
        ),
        "mac": doc.get("mac"),
        "config_version": revision(
            doc, "config_version"
        )
    }


def map_rule(
    doc: dict,
    ctx: Context
) -> dict:

    scope_type = str(
        required(doc, "scope_type")
    )

    lookup = {
        "Device": (
            "scope_ref",
            ctx.devices,
            "device_id",
            "device"
        ),
        "Device Profile": (
            "scope_profile",
            ctx.profiles,
            "profile_code",
            "device_profile"
        ),
        "Zone": (
            "scope_zone",
            ctx.zones,
            "zone_code",
            "zone"
        ),
        "Site": (
            "scope_site",
            ctx.sites,
            "site_code",
            "site"
        )
    }

    if scope_type not in lookup:
        raise ValueError(
            f"Scope {scope_type!r} needs "
            "Data Plane V2 contract; not sent in M1"
        )

    (
        link_field,
        indexed,
        code_field,
        target_type
    ) = lookup[scope_type]

    scope_ref = link_code(
        indexed,
        doc,
        link_field,
        code_field
    )

    return {
        "rule_id": required(
            doc, "rule_id"
        ),
        "version": revision(
            doc, "version"
        ),
        "rule_type": required(
            doc, "rule_type"
        ),
        "scope": {
            "type": target_type,
            "ref": scope_ref
        },
        "data_point": doc.get("data_point"),
        "operator": doc.get("operator"),
        "threshold": doc.get("threshold"),
        "duration_s": doc.get("duration_s"),
        "hysteresis": doc.get("hysteresis"),
        "clear_duration_s": doc.get(
            "clear_duration_s"
        ),
        "quiet_window_s": doc.get(
            "quiet_window_s"
        ),
        "severity": doc.get("severity"),
        "open_limit_s": doc.get("open_limit_s"),
        "schedule": doc.get("schedule"),
        "auto_close": bool(
            doc.get("auto_close")
        ),
        "active": bool(
            doc.get("active")
        )
    }