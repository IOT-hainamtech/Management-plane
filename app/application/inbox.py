from datetime import datetime
from zoneinfo import ZoneInfo

from app.application.errors import OrganizationMismatchError, is_permanent_error
from app.application.events import AlarmEvent, StatusEvent
from app.application.ports import FrappePort, StorePort


LOCAL_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def frappe_datetime(value: datetime | None) -> str | None:
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S") if value else None


class InboxService:
    def __init__(self, cfg, store: StorePort, frappe: FrappePort):
        self.cfg, self.store, self.frappe = cfg, store, frappe

    @property
    def _organization_name(self):
        return getattr(self.cfg, "nextgrp_organization_name", "")

    def _owned_device(self, device_id: str):
        device = self.frappe.find("Device", "device_id", device_id)
        if not device:
            return None
        site_name = device.get("site")
        site = self.frappe.find("Site", "name", site_name) if site_name else None
        if not site or (self._organization_name and site.get("organization") != self._organization_name):
            raise OrganizationMismatchError("Device Site is outside configured Organization")
        return device

    def _rule_for_device(self, rule_id: str, device: dict):
        rule = self.frappe.find("Rule Configuration", "rule_id", rule_id)
        if not rule:
            return None
        scope = rule.get("scope_type")
        field, expected = {
            "Device": ("scope_ref", device["name"]),
            "Device Profile": ("scope_profile", device.get("device_profile")),
            "Zone": ("scope_zone", device.get("zone")),
            "Site": ("scope_site", device.get("site")),
        }.get(scope, (None, None))
        if not field or not expected or rule.get(field) != expected:
            raise OrganizationMismatchError("Rule does not apply to Device")
        return rule

    def _alarm_for_device(self, alarm_key: str, device: dict):
        alarm = self.frappe.find("Alarm", "alarm_key", alarm_key)
        if alarm and (alarm.get("device") != device["name"] or (
            alarm.get("site") and alarm["site"] != device.get("site")
        )):
            raise OrganizationMismatchError("Alarm does not belong to Device Site")
        return alarm

    @staticmethod
    def _alarm_summary(event: AlarmEvent) -> str | None:
        data = event.event.data
        parts = [
            str(data[field]).strip()
            for field in ("summary", "description")
            if isinstance(data.get(field), str) and data[field].strip()
        ]
        return "\n\n".join(parts) or None

    def _validate_org(self, org_code: str):
        if org_code != self.cfg.org_code:
            raise OrganizationMismatchError("Organization mismatch")

    def receive_status(self, raw: dict):
        event = StatusEvent.model_validate(raw)
        self._validate_org(event.org_code)
        ack_id, duplicate = self.store.accept_event(event.event_id, "status", event.model_dump(mode="json"))
        return {"accepted": True, "duplicate": duplicate, "ack_id": ack_id, "device_id": event.device_id}

    def receive_alarm(self, raw: dict):
        event = AlarmEvent.model_validate(raw)
        self._validate_org(event.org_code)
        ack_id, duplicate = self.store.accept_event(event.event_id, "alarm", event.model_dump(mode="json"))
        return {"accepted": True, "duplicate": duplicate, "ack_id": ack_id, "alarm_key": event.alarm_key}

    def process(self, max_items: int = 100):
        counts = {"processed": 0, "failed": 0}
        for _ in range(max_items):
            item = self.store.claim_inbox()
            if item is None:
                break
            try:
                if item.kind == "status":
                    self.apply_status(item.payload)
                elif item.kind == "alarm":
                    self.apply_alarm(item.payload)
                else:
                    raise ValueError(f"Unknown inbox kind: {item.kind}")
                self.store.finish_inbox(item.event_id)
                counts["processed"] += 1
            except Exception as exc:
                permanent = is_permanent_error(exc)
                self.store.fail_inbox(item, exc, permanent=permanent)
                counts["failed"] += 1
        return counts

    def apply_status(self, raw: dict):
        event = StatusEvent.model_validate(raw)
        self._validate_org(event.org_code)
        device = self._owned_device(event.device_id)
        if not device:
            raise LookupError(f"Unknown Device: {event.device_id}")
        state_at = event.since or event.last_seen
        if state_at and self.store.status_is_stale(event.org_code, event.device_id, state_at):
            return "stale-status"
        body = {"online_state": self.cfg.device_status_online if event.state == "online" else self.cfg.device_status_offline}
        last_seen = frappe_datetime(event.last_seen)
        if last_seen and (not device.get("last_seen") or last_seen >= str(device["last_seen"])):
            body["last_seen"] = last_seen
        if event.fw_version:
            body["fw_version"] = event.fw_version
        self.frappe.update("Device", device["name"], body)
        if state_at:
            self.store.save_status_cursor(event.org_code, event.device_id, event.state, state_at)
        return "updated-status"

    def apply_alarm(self, raw: dict):
        event = AlarmEvent.model_validate(raw)
        self._validate_org(event.org_code)
        device = self._owned_device(event.device_id)
        if not device:
            raise LookupError(f"Unknown Device: {event.device_id}")
        rule = self._rule_for_device(event.rule_id, device)
        if not rule:
            raise LookupError(f"Unknown Rule: {event.rule_id}")
        existing = self._alarm_for_device(event.alarm_key, device)
        if event.kind == "occurrence":
            if existing:
                raised_at = frappe_datetime(event.raised_at)
                if int(existing.get("occurrence_count") or 0) >= event.occurrence_seq:
                    return "duplicate-occurrence"
                if existing.get("last_occurred") and raised_at < str(existing["last_occurred"]):
                    return "stale-occurrence"
                changes = {"occurrence_count": event.occurrence_seq, "last_occurred": raised_at}
                summary = self._alarm_summary(event)
                if summary:
                    changes["summary"] = summary
                if existing.get("cleared_at"):
                    if raised_at <= str(existing["cleared_at"]):
                        return "stale-occurrence"
                    changes["cleared_at"] = None
                    if existing.get("status") == self.cfg.alarm_closed_status:
                        changes["status"] = self.cfg.alarm_status_new
                self.frappe.update("Alarm", existing["name"], changes)
                return "updated-occurrence"
            zone = self.frappe.find("Zone", "zone_code", event.zone_code) if event.zone_code else None
            if event.zone_code and not zone:
                raise LookupError("Missing Rule, Device or Zone in nextGRP; sync configuration first")
            if zone and (zone.get("site") != device.get("site") or zone.get("name") != device.get("zone")):
                raise OrganizationMismatchError("Zone does not belong to Device Site")
            body = {
                "alarm_key": event.alarm_key, "rule": rule["name"], "device": device["name"],
                "site": device["site"],
                "zone": zone["name"] if zone else None, "severity": event.severity,
                "status": self.cfg.alarm_status_new,
                "first_occurred": frappe_datetime(event.episode_start),
                "last_occurred": frappe_datetime(event.raised_at),
                "occurrence_count": event.occurrence_seq,
                "source_event_id": event.event.event_id,
                "event_type": event.event.event_type,
            }
            summary = self._alarm_summary(event)
            if summary:
                body["summary"] = summary
            self.frappe.create("Alarm", {key: value for key, value in body.items() if value is not None})
            return "created-occurrence"
        if not existing:
            raise LookupError("Alarm occurrence not found")
        cleared_at = frappe_datetime(event.cleared_at)
        if existing.get("cleared_at"):
            return "already-cleared" if cleared_at <= str(existing["cleared_at"]) else "stale-cleared"
        if existing.get("last_occurred") and cleared_at < str(existing["last_occurred"]):
            return "stale-cleared"
        changes = {"cleared_at": cleared_at}
        if int(rule.get("auto_close") or 0) == 1:
            changes["status"] = self.cfg.alarm_closed_status
        self.frappe.update("Alarm", existing["name"], changes)
        return "cleared"
