import logging
import time

from app.adapters import DataPlaneClient, FrappeClient, MockDataPlane, MockFrappe
from app.db import Store
from app.service import Bridge
from app.settings import Settings


log = logging.getLogger("bridge-worker")


def main():
    logging.basicConfig(level=logging.INFO)
    cfg = Settings()
    store = Store(cfg.database_url)
    frappe = MockFrappe() if cfg.nextgrp_mode == "mock" else FrappeClient(cfg)
    plane = MockDataPlane() if cfg.data_plane_mode == "mock" else DataPlaneClient(cfg)
    bridge = Bridge(cfg, store, frappe, plane)
    try:
        while True:
            try:
                log.info("Cycle: %s", bridge.cycle())
            except Exception:
                log.exception("Cycle failed")
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        frappe.close()
        plane.close()
        store.close()


if __name__ == "__main__":
    main()
