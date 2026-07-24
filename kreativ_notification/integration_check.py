#!/usr/bin/env python
"""Integration check for kreativ_notification - must be run via bench execute with --file or as script."""

import sys
import os

# Add bench paths
sys.path.insert(0, '/home/mitesh/frappe-bench-v16/apps')
sys.path.insert(0, '/home/mitesh/frappe-bench-v16/env/lib/python3.14/site-packages')

import frappe

results = []

def test(name, fn):
    try:
        result = fn()
        results.append(f"  PASS: {name} -> {result}")
    except Exception as e:
        results.append(f"  FAIL: {name} -> {type(e).__name__}: {e}")

def main():
    site = frappe.conf.site_name

    # DB checks
    test("OpenWA Settings loaded", lambda: frappe.db.get_single_value("OpenWA Settings", "enabled") is not None)
    test("OpenWA Settings module", lambda: frappe.db.get_value("DocType", "OpenWA Settings", "module"))
    test("WhatsApp Send Log module", lambda: frappe.db.get_value("DocType", "WhatsApp Send Log", "module"))
    test("WhatsApp Send Log table", lambda: frappe.db.table_exists("WhatsApp Send Log"))
    test("Notification Rule table", lambda: frappe.db.table_exists("Notification Rule"))
    test("Message Template table", lambda: frappe.db.table_exists("Message Template"))
    test("Notification Queue table", lambda: frappe.db.table_exists("Notification Queue"))
    test("Notification Channel table", lambda: frappe.db.table_exists("Notification Channel"))
    test("OpenWA session_id populated", lambda: bool(frappe.db.get_single_value("OpenWA Settings", "session_id")))

    # Import checks
    test("import openwa_client", lambda: hasattr(__import__("kreativ_notification.notification.openwa_client", fromlist=["get_openwa_config"]), "get_openwa_config"))
    test("import send_log", lambda: hasattr(__import__("kreativ_notification.notification.send_log", fromlist=["create_log"]), "create_log"))
    test("import dispatcher", lambda: hasattr(__import__("kreativ_notification.notification.dispatcher", fromlist=["dispatch"]), "dispatch"))
    test("import rules_engine", lambda: hasattr(__import__("kreativ_notification.notification.rules_engine", fromlist=["process_notification_rule"]), "process_notification_rule"))
    test("import inbound", lambda: hasattr(__import__("kreativ_notification.notification.inbound", fromlist=["receive_whatsapp_message"]), "receive_whatsapp_message"))
    test("import check_settings", lambda: hasattr(__import__("kreativ_notification.notification.check_settings", fromlist=["check_session"]), "check_session"))
    test("import setup_defaults", lambda: hasattr(__import__("kreativ_notification.notification.setup_defaults", fromlist=["setup_defaults"]), "setup_defaults"))

    # gravures_custom integration (skip if app not installed)
    try:
        test("gravures_custom.log_whatsapp_send", lambda: hasattr(__import__("gravures_custom.overrides", fromlist=["log_whatsapp_send"]), "log_whatsapp_send"))
        test("gravures_custom.send_proofing_whatsapp", lambda: hasattr(__import__("gravures_custom.overrides", fromlist=["send_proofing_whatsapp"]), "send_proofing_whatsapp"))
    except ImportError:
        results.append("  SKIP: gravures_custom not installed")

    # Hook checks
    hooks = __import__("kreativ_notification.hooks", fromlist=["app_include_js", "scheduler_events"])
    test("app_include_js set", lambda: bool(hooks.app_include_js))
    test("scheduler_events set", lambda: bool(hooks.scheduler_events))

    print(f"\n{'='*50}")
    print(f"Results for {site}:")
    print(f"{'='*50}")
    for r in results:
        print(r)

    passed = sum(1 for r in results if "PASS" in r)
    failed = sum(1 for r in results if "FAIL" in r)
    print(f"\nTotal: {passed} passed, {failed} failed out of {len(results)}")

if __name__ == "__main__":
    # When run via bench execute, site is already initialized
    # When run directly, need to init
    if not frappe.get_conf().get("site_name"):
        frappe.init(site="kreativ216")
        frappe.connect()
    main()