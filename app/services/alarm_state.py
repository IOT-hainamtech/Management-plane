from app.infrastructure.store import digest


class AlarmStateSync:

    def __init__(
        self,
        frappe,
        plane,
        store,
        state_map
    ):
        self.frappe = frappe
        self.plane = plane
        self.store = store
        self.state_map = state_map

    async def sync_once(self):

        result = {
            "sent": [],
            "errors": []
        }

        if not self.state_map:
            return result

        alarms = await self.frappe.all(
            "Alarm"
        )

        for alarm in alarms:

            state = self.state_map.get(
                alarm.get("status")
            )

            if not state:
                continue

            key = alarm.get("alarm_key")

            if not key:
                continue

            payload = {
                "alarm_key": key,
                "state": state,
                "changed_at": alarm.get(
                    "modified"
                ),
                "changed_by": alarm.get(
                    "modified_by"
                )
            }

            fingerprint = digest({
                "state": state
            })

            previous = await self.store.sync_entry(
                "alarm_state",
                key
            )

            if (
                previous
                and previous[1] == fingerprint
            ):
                continue

            try:
                await self.plane.alarm_state(
                    key,
                    payload
                )

                version = (
                    previous[0] + 1
                    if previous else 1
                )

                await self.store.save_sync(
                    "alarm_state",
                    key,
                    version,
                    fingerprint
                )

                result["sent"].append(key)

            except Exception as exc:
                result["errors"].append({
                    "item": key,
                    "error": str(exc)[:200]
                })

        return result