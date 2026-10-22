import logging

from app.domain.mapping import (
    Context,
    index,
    map_profile,
    map_device,
    map_rule
)

from app.infrastructure.store import digest


logger = logging.getLogger(__name__)


class ConfigSync:

    def __init__(
        self,
        frappe,
        plane,
        store
    ):
        self.frappe = frappe
        self.plane = plane
        self.store = store
    async def _load(self):
        names = [
            "Device Profile",
            "Device",
            "Rule Configuration",
            "Site",
            "Zone"
        ]

        raw = {
            name: await self.frappe.all(name)
            for name in names
        }

        ctx = Context(
            profiles=index(raw["Device Profile"]),
            devices=index(raw["Device"]),
            sites=index(raw["Site"]),
            zones=index(raw["Zone"])
        )

        return raw, ctx

    async def snapshot(self) -> dict:
        raw, ctx = await self._load()

        return {
            "profiles": [
                map_profile(doc)
                for doc in raw["Device Profile"]
            ],
            "devices": [
                map_device(doc, ctx)
                for doc in raw["Device"]
            ],
            "rules": [
                map_rule(doc, ctx)
                for doc in raw["Rule Configuration"]
            ]
        }
    async def sync_once(self) -> dict:

        names = [
            "Device Profile",
            "Device",
            "Rule Configuration",
            "Site",
            "Zone"
        ]

        raw = {
            name: await self.frappe.all(name)
            for name in names
        }

        ctx = Context(
            profiles=index(
                raw["Device Profile"]
            ),
            devices=index(
                raw["Device"]
            ),
            sites=index(
                raw["Site"]
            ),
            zones=index(
                raw["Zone"]
            )
        )

        plan = [
            (
                "profile",
                raw["Device Profile"],
                map_profile,
                self.plane.put_profile,
                "profile_code",
                "version"
            ),
            (
                "device",
                raw["Device"],
                lambda doc: map_device(
                    doc, ctx
                ),
                self.plane.put_device,
                "device_id",
                "config_version"
            ),
            (
                "rule",
                raw["Rule Configuration"],
                lambda doc: map_rule(
                    doc, ctx
                ),
                self.plane.put_rule,
                "rule_id",
                "version"
            )
        ]

        result = {
            "sent": [],
            "skipped": [],
            "errors": []
        }

        for (
            kind,
            documents,
            mapper,
            sender,
            key_field,
            revision_field
        ) in plan:

            for document in documents:

                label = (
                    f"{kind}/"
                    f"{document.get(key_field, document.get('name', '?'))}"
                )

                try:
                    payload = mapper(document)

                    key = str(
                        payload[key_field]
                    )

                    version = int(
                        payload[revision_field]
                    )

                    fingerprint = digest(
                        payload
                    )

                    previous = (
                        await self.store.sync_entry(
                            kind, key
                        )
                    )

                    if previous:
                        old_version, old_hash = previous

                        if version < old_version:
                            raise ValueError(
                                "Version decreased; "
                                "increase version and retry"
                            )

                        if (
                            version == old_version
                            and fingerprint != old_hash
                        ):
                            raise ValueError(
                                "Payload changed without "
                                "version increase"
                            )

                        if (
                            version == old_version
                            and fingerprint == old_hash
                        ):
                            result["skipped"].append(
                                label
                            )
                            continue

                    await sender(
                        key,
                        payload
                    )

                    await self.store.save_sync(
                        kind,
                        key,
                        version,
                        fingerprint
                    )

                    result["sent"].append(
                        label
                    )

                except Exception as exc:
                    logger.warning(
                        "Sync failed %s: %s",
                        label,
                        exc
                    )

                    result["errors"].append({
                        "item": label,
                        "error": str(exc)[:200]
                    })

        return result