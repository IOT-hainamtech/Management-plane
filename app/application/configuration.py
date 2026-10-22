"""Scan Frappe configuration and deliver independent jobs in dependency order."""

import hashlib
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.resources import dependencies_for
from app.domain.mapping import plan_results, plans
from app.application.errors import is_permanent_error
from app.application.ports import DataPlanePort, FrappePort, StorePort


LOCAL_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DOC_TYPES = ("Site", "Zone", "Device Profile", "Device", "Rule Configuration")
log = logging.getLogger(__name__)


def modified_revision(value: object) -> int:
    if not value:
        return 1
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=LOCAL_TZ)
        return max(1, int(stamp.timestamp() * 1_000_000))
    except ValueError:
        return int(hashlib.sha256(str(value).encode()).hexdigest()[:15], 16)


class ConfigurationSync:
    def __init__(self, cfg, store: StorePort, frappe: FrappePort, plane: DataPlanePort):
        self.cfg, self.store, self.frappe, self.plane = cfg, store, frappe, plane

    def _organization_filters(self):
        name = getattr(self.cfg, "nextgrp_organization_name", "")
        return [["organization", "=", name]] if name else None

    def snapshot(self):
        docs = {name: self.frappe.all(name, filters=self._organization_filters()) for name in DOC_TYPES}
        return plans(docs, self.cfg.org_code, self.cfg.org_name)

    def stage_configuration(self):
        docs = {name: self.frappe.all(name, filters=self._organization_filters()) for name in DOC_TYPES}
        mapped, errors = plan_results(docs, self.cfg.org_code, self.cfg.org_name)
        resources = []
        for kind, key, priority, revision, payload in mapped:
            parents = [ref.as_json() for ref in dependencies_for(kind, payload)]
            resources.append((kind, key, priority, revision, payload, parents))
        counts = self.store.stage_many(resources, self.cfg.sync_batch_size)
        errors.extend(counts.pop("invalid", []))
        if errors:
            for error in errors:
                log.error("Invalid configuration resource: %s", error)
        return {**counts, "invalid": errors}

    def stage_alarm_states(self):
        states = {
            self.cfg.alarm_ack_status: "acknowledged",
            self.cfg.alarm_processing_status: "in_progress",
            self.cfg.alarm_closed_status: "closed",
        }
        resources = []
        for alarm in self.frappe.all("Alarm", filters=self._organization_filters()):
            mapped, key = states.get(alarm.get("status")), alarm.get("alarm_key")
            if not mapped or not key:
                continue
            revision = modified_revision(alarm.get("modified"))
            payload = {
                "org_code": self.cfg.org_code, "alarm_key": key,
                "state": mapped, "version": revision, "changed_at": alarm.get("modified"),
            }
            resources.append(("alarm_state", str(key), 60, revision, payload, []))
        return self.store.stage_many(resources, self.cfg.sync_batch_size)

    def deliver(self, max_items: int = 100):
        result = {"sent": [], "failed": 0, "blocked": False}
        for _ in range(max_items):
            job = self.store.claim_config()
            if job is None:
                break
            try:
                reply = self.plane.send(job.kind, job.resource_key, job.payload)
                applied = reply.get("applied_version")
                if job.kind != "alarm_state" and (isinstance(applied, bool) or applied != job.version):
                    raise ValueError(f"Data Plane applied version {applied}, expected {job.version}")
                self.store.finish_config(job.kind, job.resource_key)
                result["sent"].append(f"{job.kind}/{job.resource_key}")
            except Exception as exc:
                permanent = is_permanent_error(exc)
                self.store.fail_config(job, exc, permanent=permanent)
                result["failed"] += 1
        # A failed job may be waiting for retry while its children remain pending.
        result["blocked"] = result["failed"] > 0
        return result
