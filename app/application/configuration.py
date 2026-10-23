
import hashlib
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.resources import dependencies_for
from app.domain.mapping import plan_results, plans
from app.application.errors import is_permanent_error
from app.application.ports import DataPlanePort, FrappePort, StorePort


LOCAL_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

DOC_TYPES = (
    "Site",
    "Zone",
    "Device Profile",
    "Device",
    "Rule Configuration",
)

log = logging.getLogger(__name__)


def modified_revision(value: object) -> int:
    if not value:
        return 1

    try:
        stamp = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=LOCAL_TZ)

        return max(1, int(stamp.timestamp() * 1_000_000))

    except ValueError:
        return int(
            hashlib.sha256(
                str(value).encode()
            ).hexdigest()[:15],
            16,
        )


class ConfigurationSync:

    def __init__(
        self,
        cfg,
        store: StorePort,
        frappe: FrappePort,
        plane: DataPlanePort,
    ):
        self.cfg = cfg
        self.store = store
        self.frappe = frappe
        self.plane = plane

    def _organization_filters(self):
        org_name = str(
            getattr(
                self.cfg,
                "nextgrp_organization_name",
                "",
            ) or ""
        ).strip()

        if not org_name:
            raise ValueError(
                "NEXTGRP_ORGANIZATION_NAME is required"
            )

        return [["organization", "=", org_name]]

    @staticmethod
    def _names(rows):
        return sorted({
            str(row["name"])
            for row in rows
            if row.get("name")
        })

    @staticmethod
    def _dedupe(rows):
        result = {}

        for row in rows:
            name = row.get("name")

            if not name:
                raise ValueError(
                    "Frappe returned a document without name"
                )

            result[str(name)] = row

        return [
            result[key]
            for key in sorted(result)
        ]

    def _scoped_sites(self):
        """
        Site is the tenant boundary.

        Site.organization is verified on LIVE.
        Never fetch all Sites without a filter.
        """
        sites = self.frappe.all(
            "Site",
            filters=self._organization_filters(),
        )

        sites = self._dedupe(sites)
        names = self._names(sites)

        if not names:
            raise ValueError(
                "No accessible Sites for configured Organization"
            )

        return sites, names

    def _by_link(
        self,
        doctype,
        field,
        allowed_names,
        chunk_size=30,
    ):
        """
        Read only records linked to previously
        authorized parent resources.

        An empty parent list MUST return [].
        Never fall back to an unfiltered read.
        """
        allowed = sorted({
            str(name)
            for name in allowed_names
            if name
        })

        if not allowed:
            return []

        allowed_set = set(allowed)
        records = []

        for index in range(0, len(allowed), chunk_size):
            chunk = allowed[index:index + chunk_size]

            rows = self.frappe.all(
                doctype,
                filters=[[field, "in", chunk]],
            )

            for row in rows:
                actual = str(row.get(field) or "")

                if actual not in allowed_set:
                    raise ValueError(
                        f"{doctype} returned a document "
                        f"outside allowed {field} scope"
                    )

                records.append(row)

        return self._dedupe(records)

    def _scoped_configuration_docs(self):
        """
        Verified LIVE relationships:

        Organization
          -> Site.organization
          -> Zone.site
          -> Device.site
          -> Device.device_profile

        Rule Configuration is selected through the verified parent link
        matching its scope_type. No unfiltered Rule scan is performed.
        """

        # 1. Fetch Sites authorized for this Organization
        sites, site_names = self._scoped_sites()

        # 2. Fetch Zones belonging to those Sites
        zones = self._by_link(
            "Zone",
            "site",
            site_names,
        )
        zone_names = self._names(zones)

        # 3. Fetch Devices belonging to those Sites
        devices = self._by_link(
            "Device",
            "site",
            site_names,
        )
        device_names = self._names(devices)

        # 4. Fetch only referenced Device Profiles
        profile_names = sorted({
            str(device["device_profile"])
            for device in devices
            if device.get("device_profile")
        })

        profiles = self._by_link(
            "Device Profile",
            "name",
            profile_names,
        )

        scope_fields = {
            "Site": ("scope_site", set(site_names)),
            "Zone": ("scope_zone", set(zone_names)),
            "Device": ("scope_ref", set(device_names)),
            "Device Profile": ("scope_profile", set(profile_names)),
        }
        rules = []
        for scope_type, (field, allowed) in scope_fields.items():
            for rule in self._by_link("Rule Configuration", field, allowed):
                if rule.get("scope_type") != scope_type:
                    continue
                if any(
                    rule.get(other_field) and str(rule[other_field]) not in other_allowed
                    for other_field, other_allowed in scope_fields.values()
                ):
                    log.warning("Skipping Rule %s: out-of-scope link", rule.get("name"))
                    continue
                rules.append(rule)
        rules = self._dedupe(rules)

        log.info(
            "LIVE scoped resources: "
            "sites=%d zones=%d devices=%d "
            "profiles=%d rules=%d",
            len(sites),
            len(zones),
            len(devices),
            len(profiles),
            len(rules),
        )

        return {
            "Site": sites,
            "Zone": zones,
            "Device Profile": profiles,
            "Device": devices,
            "Rule Configuration": rules,
        }

    def snapshot(self):
        docs = self._scoped_configuration_docs()

        return plans(
            docs,
            self.cfg.org_code,
            self.cfg.org_name,
        )

    def stage_configuration(self):
        docs = self._scoped_configuration_docs()

        mapped, errors = plan_results(
            docs,
            self.cfg.org_code,
            self.cfg.org_name,
        )

        resources = []

        for kind, key, priority, revision, payload in mapped:
            parents = [
                ref.as_json()
                for ref in dependencies_for(kind, payload)
            ]

            resources.append(
                (
                    kind,
                    key,
                    priority,
                    revision,
                    payload,
                    parents,
                )
            )

        counts = self.store.stage_many(
            resources,
            self.cfg.sync_batch_size,
        )

        errors.extend(
            counts.pop("invalid", [])
        )

        for error in errors:
            log.error(
                "Invalid configuration resource: %s",
                error,
            )

        return {
            **counts,
            "invalid": errors,
        }

    def stage_alarm_states(self):
        """
        Only read Alarms linked to authorized Sites.

        Alarm.site filter is verified on LIVE.
        Device-only alarms without a Site link
        are not included until separately verified.
        """

        states = {
            self.cfg.alarm_ack_status: "acknowledged",
            self.cfg.alarm_processing_status: "in_progress",
            self.cfg.alarm_closed_status: "closed",
        }

        _, site_names = self._scoped_sites()

        alarms = self._by_link(
            "Alarm",
            "site",
            site_names,
        )

        resources = []

        for alarm in alarms:
            mapped = states.get(
                alarm.get("status")
            )
            key = alarm.get("alarm_key")

            if not mapped or not key:
                continue

            revision = modified_revision(
                alarm.get("modified")
            )

            payload = {
                "org_code": self.cfg.org_code,
                "alarm_key": str(key),
                "state": mapped,
                "version": revision,
                "changed_at": alarm.get("modified"),
            }

            resources.append(
                (
                    "alarm_state",
                    str(key),
                    60,
                    revision,
                    payload,
                    [],
                )
            )

        return self.store.stage_many(
            resources,
            self.cfg.sync_batch_size,
        )

    def deliver(self, max_items: int = 100):
        result = {
            "sent": [],
            "failed": 0,
            "blocked": False,
        }

        for _ in range(max_items):
            job = self.store.claim_config()

            if job is None:
                break

            try:
                reply = self.plane.send(
                    job.kind,
                    job.resource_key,
                    job.payload,
                )

                applied = reply.get("applied_version")

                if job.kind != "alarm_state":
                    if (
                        isinstance(applied, bool)
                        or applied != job.version
                    ):
                        raise ValueError(
                            "Data Plane applied version "
                            f"{applied}, expected {job.version}"
                        )

                self.store.finish_config(
                    job.kind,
                    job.resource_key,
                )

                result["sent"].append(
                    f"{job.kind}/{job.resource_key}"
                )

            except Exception as exc:
                permanent = is_permanent_error(exc)

                self.store.fail_config(
                    job,
                    exc,
                    permanent=permanent,
                )

                result["failed"] += 1

        result["blocked"] = (
            result["failed"] > 0
        )

        return result
