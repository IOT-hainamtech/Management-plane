import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.db import ConfigJob, Store
from app.mapping import plans
from app.schemas import AlarmEvent, StatusEvent


LOCAL_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def frappe_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")


def modified_revision(value: object) -> int:
    """Produce a stable positive revision from Frappe's modified timestamp."""
    if not value:
        return 1
    try:
        raw = str(value).replace("Z", "+00:00")
        stamp = datetime.fromisoformat(raw)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=LOCAL_TZ)
        return max(1, int(stamp.timestamp() * 1_000_000))
    except ValueError:
        return int(hashlib.sha256(str(value).encode()).hexdigest()[:15], 16)


class Bridge:
    def __init__(self, cfg, store: Store, frappe, plane):
        self.cfg, self.store, self.frappe, self.plane = cfg, store, frappe, plane

    def snapshot(self) -> list[tuple[str, str, int, int, dict]]:
        docs = {name: self.frappe.all(name) for name in ("Site", "Zone", "Device Profile", "Device", "Rule Configuration")}
        return plans(docs, self.cfg.org_code, self.cfg.org_name)

    def stage_configuration(self) -> dict[str, int]:
        result = {"staged": 0, "restaged": 0, "unchanged": 0}
        for kind, key, priority, revision, payload in self.snapshot():
            result[self.store.stage(kind, key, priority, revision, payload)] += 1
        return result

    def stage_alarm_states(self) -> dict[str, int]:
        states = {
            self.cfg.alarm_ack_status: "acknowledged",
            self.cfg.alarm_processing_status: "in_progress",
            self.cfg.alarm_closed_status: "closed",
        }
        result = {"staged": 0, "restaged": 0, "unchanged": 0}
        for alarm in self.frappe.all("Alarm"):
            mapped = states.get(alarm.get("status"))
            key = alarm.get("alarm_key")
            if not mapped or not key:
                continue
            revision = modified_revision(alarm.get("modified"))
            payload = {"org_code": self.cfg.org_code, "alarm_key": key, "state": mapped, "version": revision, "changed_at": alarm.get("modified")}
            result[self.store.stage("alarm_state", str(key), 60, revision, payload)] += 1
        return result

    def flush_configuration(self, max_items: int = 100) -> dict[str, object]:
        result: dict[str, object] = {"sent": [], "failed": 0, "blocked": False}
        for _ in range(max_items):
            job = self.store.claim_config()
            if job is None:
                break
            try:
                reply = self.plane.send(job.kind, job.resource_key, job.payload)
                applied = reply.get("applied_version")
                if applied is not None and int(applied) != job.version:
                    raise ValueError(f"Data Plane applied version {applied}, expected {job.version}")
                self.store.finish_config(job.kind, job.resource_key)
                result["sent"].append(f"{job.kind}/{job.resource_key}")
            except Exception as exc:
                permanent = isinstance(exc, ValueError) or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {400, 401, 403, 404, 409, 422})
                self.store.fail_config(job, exc, permanent=permanent)
                result["failed"] = int(result["failed"]) + 1
                result["blocked"] = True
                break
        return result

    def receive_status(self, raw: dict) -> dict:
        event = StatusEvent.model_validate(raw)
        self._validate_org(event.org_code)
        ack_id, duplicate = self.store.accept_event(event.event_id, "status", event.model_dump(mode="json"))
        return {"accepted": True, "duplicate": duplicate, "ack_id": ack_id, "device_id": event.device_id}

    def receive_alarm(self, raw: dict) -> dict:
        event = AlarmEvent.model_validate(raw)
        self._validate_org(event.org_code)
        ack_id, duplicate = self.store.accept_event(event.event_id, "alarm", event.model_dump(mode="json"))
        return {"accepted": True, "duplicate": duplicate, "ack_id": ack_id, "alarm_key": event.alarm_key}

    def process_inbox(self, max_items: int = 100) -> dict[str, int]:
        result = {"processed": 0, "failed": 0}
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
                result["processed"] += 1
            except Exception as exc:
                permanent = isinstance(exc, ValueError) or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {400, 401, 403, 404, 422})
                self.store.fail_inbox(item, exc, permanent=permanent)
                result["failed"] += 1
        return result

    def apply_status(self, raw: dict):
        event = StatusEvent.model_validate(raw)
        device = self.frappe.find("Device", "device_id", event.device_id)
        if not device:
            raise LookupError(f"Unknown Device: {event.device_id}")
        state = self.cfg.device_status_online if event.state == "online" else self.cfg.device_status_offline
        body = {"online_state": state}
        last_seen = frappe_datetime(event.last_seen)
        if last_seen:
            if device.get("last_seen") and last_seen < str(device["last_seen"]):
                return
            body["last_seen"] = last_seen
        if event.fw_version:
            body["fw_version"] = event.fw_version
        self.frappe.update("Device", device["name"], body)

    def apply_alarm(self, raw: dict):
        event = AlarmEvent.model_validate(raw)
        existing = self.frappe.find("Alarm", "alarm_key", event.alarm_key)
        if event.kind == "occurrence":
            if existing:
                if int(existing.get("occurrence_count") or 0) >= event.occurrence_seq:
                    return
                self.frappe.update("Alarm", existing["name"], {"occurrence_count": event.occurrence_seq, "last_occurred": frappe_datetime(event.raised_at)})
                return
            rule = self.frappe.find("Rule Configuration", "rule_id", event.rule_id)
            device = self.frappe.find("Device", "device_id", event.device_id)
            zone = self.frappe.find("Zone", "zone_code", event.zone_code) if event.zone_code else None
            if not rule or not device or (event.zone_code and not zone):
                raise LookupError("Missing Rule, Device or Zone in nextGRP; sync configuration first")
            body = {"alarm_key": event.alarm_key, "rule": rule["name"], "device": device["name"], "zone": zone["name"] if zone else None, "severity": event.severity, "status": self.cfg.alarm_status_new, "first_occurred": frappe_datetime(event.episode_start or event.raised_at), "last_occurred": frappe_datetime(event.raised_at), "occurrence_count": event.occurrence_seq, "source_event_id": event.event_id}
            self.frappe.create("Alarm", {key: value for key, value in body.items() if value is not None})
            return
        if not existing:
            raise LookupError("Alarm not found yet; retry cleared after occurrence")
        if existing.get("status") == self.cfg.alarm_status_cleared:
            return
        self.frappe.update("Alarm", existing["name"], {"status": self.cfg.alarm_status_cleared, "cleared_at": frappe_datetime(event.cleared_at)})

    def cycle(self) -> dict:
        with self.store.cycle_lock() as acquired:
            if not acquired:
                return {"skipped": "another bridge worker owns the cycle"}
            staged = self.stage_configuration()
            sent = self.flush_configuration()
            inbox = self.process_inbox()
            alarms = self.stage_alarm_states()
            alarm_sent = self.flush_configuration()
            return {"config": {"staged": staged, "delivery": sent}, "inbox": inbox, "alarm_states": {"staged": alarms, "delivery": alarm_sent}}

    def _validate_org(self, org_code: str):
        if org_code != self.cfg.org_code:
            raise ValueError(f"Unexpected org_code: {org_code}")
