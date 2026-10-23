import logging

from app.application.configuration import ConfigurationSync
from app.application.inbox import InboxService
from app.application.ports import DataPlanePort, FrappePort, StorePort


log = logging.getLogger(__name__)


class Bridge:
    def __init__(self, cfg, store: StorePort, frappe: FrappePort, plane: DataPlanePort):
        self.cfg, self.store, self.frappe, self.plane = cfg, store, frappe, plane
        self.configuration = ConfigurationSync(cfg, store, frappe, plane)
        self.inbox = InboxService(cfg, store, frappe)

    # Keep these use cases stable for the HTTP API and existing callers.
    def snapshot(self):
        return self.configuration.snapshot()

    def stage_configuration(self):
        return self.configuration.stage_configuration()

    def stage_alarm_states(self):
        return self.configuration.stage_alarm_states()

    def flush_configuration(self, max_items: int = 100):
        return self.configuration.deliver(max_items)

    def receive_status(self, raw: dict):
        return self.inbox.receive_status(raw)

    def receive_alarm(self, raw: dict):
        return self.inbox.receive_alarm(raw)

    def process_inbox(self, max_items: int = 100):
        return self.inbox.process(max_items)

    def apply_status(self, raw: dict):
        return self.inbox.apply_status(raw)

    def apply_alarm(self, raw: dict):
        return self.inbox.apply_alarm(raw)

    def cycle(self):
        with self.store.cycle_lock() as acquired:
            if not acquired:
                return {"skipped": "another bridge worker owns the cycle"}
            # Reclaim rows left in processing after a crash. The cycle lock
            # prevents an active worker from having its rows stolen.
            self.store.recover_processing()
            try:
                staged = self.stage_configuration()
                delivered = self.flush_configuration(self.cfg.sync_batch_size)
                config = {"staged": staged, "delivery": delivered}
            except Exception as exc:
                log.exception("Configuration scan or delivery failed")
                config = {"error": str(exc)[:200]}
            # Inbound events must still make progress when a configuration
            # document is invalid or Frappe's config listing is unavailable.
            inbox = self.process_inbox(self.cfg.inbox_batch_size)
            try:
                alarm_states = self.stage_alarm_states()
                alarm_delivery = self.flush_configuration(self.cfg.sync_batch_size)
                alarm_config = {"staged": alarm_states, "delivery": alarm_delivery}
            except Exception as exc:
                log.exception("Alarm state scan or delivery failed")
                alarm_config = {"error": str(exc)[:200]}
            return {
                "config": config,
                "inbox": inbox,
                "alarm_states": alarm_config,
            }
