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

    def _filters(self):
        return [["organization", "=", self._organization_name]] if self._organization_name else None

    def _find_owned(self, doctype: str, field: str, value: str):
        document = self.frappe.find(doctype, field, value, filters=self._filters())
        if document and getattr(self.cfg, "nextgrp_mode", "mock") == "live":
            if document.get("organization") != self._organization_name:
                raise OrganizationMismatchError("Frappe document organization mismatch")
        return document

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
        device = self._find_owned("Device", "device_id", event.device_id)
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
        existing = self._find_owned("Alarm", "alarm_key", event.alarm_key)
        if event.kind == "occurrence":
            if existing:
                raised_at = frappe_datetime(event.raised_at)
                if int(existing.get("occurrence_count") or 0) >= event.occurrence_seq:
                    return "duplicate-occurrence"
                if existing.get("last_occurred") and raised_at < str(existing["last_occurred"]):
                    return "stale-occurrence"
                changes = {"occurrence_count": event.occurrence_seq, "last_occurred": raised_at}
                if existing.get("cleared_at"):
                    if raised_at <= str(existing["cleared_at"]):
                        return "stale-occurrence"
                    changes["cleared_at"] = None
                    if existing.get("status") == self.cfg.alarm_closed_status:
                        changes["status"] = self.cfg.alarm_status_new
                self.frappe.update("Alarm", existing["name"], changes)
                return "updated-occurrence"
            rule = self._find_owned("Rule Configuration", "rule_id", event.rule_id)
            device = self._find_owned("Device", "device_id", event.device_id)
            zone = self._find_owned("Zone", "zone_code", event.zone_code) if event.zone_code else None
            if not rule or not device or (event.zone_code and not zone):
                raise LookupError("Missing Rule, Device or Zone in nextGRP; sync configuration first")
            body = {
                "alarm_key": event.alarm_key, "rule": rule["name"], "device": device["name"],
                "zone": zone["name"] if zone else None, "severity": event.severity,
                "status": self.cfg.alarm_status_new,
                "first_occurred": frappe_datetime(event.episode_start),
                "last_occurred": frappe_datetime(event.raised_at),
                "occurrence_count": event.occurrence_seq,
                "source_event_id": event.event.event_id,
                "event_type": event.event.event_type,
            }
            if self._organization_name:
                body["organization"] = self._organization_name
            self.frappe.create("Alarm", {key: value for key, value in body.items() if value is not None})
            return "created-occurrence"
        if not existing:
            raise LookupError("Alarm occurrence not found")
        cleared_at = frappe_datetime(event.cleared_at)
        if existing.get("cleared_at"):
            return "already-cleared" if cleared_at <= str(existing["cleared_at"]) else "stale-cleared"
        if existing.get("last_occurred") and cleared_at < str(existing["last_occurred"]):
            return "stale-cleared"
        rule = self._find_owned("Rule Configuration", "rule_id", event.rule_id)
        if not rule:
            raise LookupError("Rule not found")
        changes = {"cleared_at": cleared_at}
        if int(rule.get("auto_close") or 0) == 1:
            changes["status"] = self.cfg.alarm_closed_status
        self.frappe.update("Alarm", existing["name"], changes)
        return "cleared"
