from datetime import datetime
from zoneinfo import ZoneInfo

from app.schemas import (
    StatusEvent,
    AlarmEvent
)


LOCAL_TZ = ZoneInfo(
    "Asia/Ho_Chi_Minh"
)


def frappe_dt(
    value: str | None
) -> str | None:

    if not value:
        return None

    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if parsed.tzinfo is None:
        raise ValueError(
            "Timestamps require an explicit timezone"
        )

    return (
        parsed
        .astimezone(LOCAL_TZ)
        .strftime("%Y-%m-%d %H:%M:%S")
    )


class EventSync:

    def __init__(
        self,
        frappe,
        settings
    ):
        self.frappe = frappe
        self.settings = settings

    async def status(
        self,
        raw: dict
    ):
        event = StatusEvent.model_validate(
            raw
        )

        device = await self.frappe.find(
            "Device",
            "device_id",
            event.device_id
        )

        if not device:
            raise LookupError(
                f"Unknown Device: {event.device_id}"
            )

        mapped = (
            self.settings.online_map.get(
                event.state
            )
        )

        if not mapped:
            raise ValueError(
                "Configure "
                "FRAPPE_ONLINE_STATE_MAP"
            )

        body = {
            "online_state": mapped
        }

        if event.last_seen:
            incoming = frappe_dt(
                event.last_seen
            )

            if (
                device.get("last_seen")
                and incoming < str(
                    device["last_seen"]
                )
            ):
                return {
                    "ignored": "stale last_seen"
                }

            body["last_seen"] = incoming

        if event.fw_version:
            body["fw_version"] = (
                event.fw_version
            )

        await self.frappe.update(
            "Device",
            device["name"],
            body
        )

        return {
            "updated": device["name"]
        }

    async def alarm(
        self,
        raw: dict
    ):
        event = AlarmEvent.model_validate(
            raw
        )

        existing = await self.frappe.find(
            "Alarm",
            "alarm_key",
            event.alarm_key
        )

        if event.kind == "occurrence":

            if existing:

                if int(
                    existing.get(
                        "occurrence_count"
                    ) or 0
                ) >= event.occurrence_seq:
                    return {
                        "ignored": "duplicate occurrence"
                    }

                body = {
                    "occurrence_count": (
                        event.occurrence_seq
                    ),
                    "last_occurred": frappe_dt(
                        event.raised_at
                    )
                }

                await self.frappe.update(
                    "Alarm",
                    existing["name"],
                    body
                )

                return {
                    "updated": existing["name"]
                }

            rule = await self.frappe.find(
                "Rule Configuration",
                "rule_id",
                event.rule_id
            )

            device = await self.frappe.find(
                "Device",
                "device_id",
                event.device_id
            )

            zone = None

            if event.zone_code:
                zone = await self.frappe.find(
                    "Zone",
                    "zone_code",
                    event.zone_code
                )

            if (
                not rule
                or not device
                or (
                    event.zone_code
                    and not zone
                )
            ):
                raise LookupError(
                    "Missing Rule, Device or Zone "
                    "in nextGRP; sync configuration first"
                )

            if not self.settings.alarm_new:
                raise ValueError(
                    "Set FRAPPE_ALARM_NEW_STATUS"
                )

            body = {
                "alarm_key": event.alarm_key,
                "rule": rule["name"],
                "device": device["name"],
                "zone": (
                    zone["name"]
                    if zone else None
                ),
                "severity": event.severity,
                "status": (
                    self.settings.alarm_new
                ),
                "first_occurred": frappe_dt(
                    event.episode_start
                    or event.raised_at
                ),
                "last_occurred": frappe_dt(
                    event.raised_at
                ),
                "occurrence_count": (
                    event.occurrence_seq
                ),
                "source_event_id": (
                    event.event.get("event_id")
                    or event.event_id
                ),
                "event_type": event.event.get(
                    "event_type"
                )
            }

            body = {
                key: value
                for key, value in body.items()
                if value is not None
            }

            created = await self.frappe.create(
                "Alarm",
                body
            )

            return {
                "created": created["name"]
            }

        # kind == "cleared"

        if not existing:
            raise LookupError(
                "Alarm not found yet; "
                "retry cleared after occurrence"
            )

        if not self.settings.alarm_cleared:
            raise ValueError(
                "Set FRAPPE_ALARM_CLEARED_STATUS"
            )

        if (
            existing.get("cleared_at")
            and existing.get("status")
            == self.settings.alarm_cleared
        ):
            return {
                "ignored": "already cleared"
            }

        await self.frappe.update(
            "Alarm",
            existing["name"],
            {
                "status": (
                    self.settings.alarm_cleared
                ),
                "cleared_at": frappe_dt(
                    event.cleared_at
                    or event.raised_at
                )
            }
        )

        return {
            "updated": existing["name"]
        }