"""One place to assemble and close the Bridge's external connections."""

from contextlib import contextmanager

from app.infrastructure.clients import DataPlaneClient, FrappeClient, MockDataPlane, MockFrappe
from app.infrastructure.database import Store
from app.application.bridge import Bridge


@contextmanager
def open_bridge(cfg):
    store = Store(cfg.database_url)
    frappe = None
    plane = None
    try:
        store.health()
        frappe = MockFrappe() if cfg.nextgrp_mode == "mock" else FrappeClient(cfg)
        plane = MockDataPlane() if cfg.data_plane_mode == "mock" else DataPlaneClient(cfg)
        yield Bridge(cfg, store, frappe, plane)
    finally:
        if plane is not None:
            plane.close()
        if frappe is not None:
            frappe.close()
        store.close()
