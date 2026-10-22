"""Dependencies between configuration resources sent to the Data Plane."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceRef:
    kind: str
    key: str

    def as_json(self) -> dict[str, str]:
        return {"kind": self.kind, "key": self.key}


def dependencies_for(kind: str, payload: dict) -> tuple[ResourceRef, ...]:
    """Return only the parents required by this resource, not a global priority barrier."""
    org = ResourceRef("organization", payload["org_code"])
    if kind == "organization":
        return ()
    if kind == "site":
        return (org,)
    if kind == "zone":
        return (ResourceRef("site", payload["site_code"]),)
    if kind == "profile":
        return (org,)
    if kind == "device":
        parents = [ResourceRef("site", payload["site_code"]), ResourceRef("profile", payload["profile_code"])]
        if payload.get("zone_code"):
            parents.append(ResourceRef("zone", payload["zone_code"]))
        return tuple(parents)
    if kind == "rule":
        scope = payload["scope"]
        parent_kind = {"DEVICE": "device", "PROFILE": "profile", "ZONE": "zone", "SITE": "site"}[scope["type"]]
        return (ResourceRef(parent_kind, scope["ref"]),)
    if kind == "alarm_state":
        return ()
    raise ValueError(f"Unknown configuration kind: {kind}")
