from types import SimpleNamespace

from app.application.configuration import ConfigurationSync
from app.infrastructure.clients.mock import MockFrappe


def test_live_scope_includes_device_rules_without_unfiltered_rule_scan():
    frappe = MockFrappe()
    frappe.docs["Rule Configuration"].append({
        **frappe.docs["Rule Configuration"][0],
        "name": "RULE-OUTSIDE", "rule_id": "OUTSIDE", "scope_ref": "OTHER-DEVICE",
    })
    cfg = SimpleNamespace(nextgrp_organization_name="Demo Organization")
    docs = ConfigurationSync(cfg, None, frappe, None)._scoped_configuration_docs()

    assert [rule["rule_id"] for rule in docs["Rule Configuration"]] == ["TEMP-HIGH"]
