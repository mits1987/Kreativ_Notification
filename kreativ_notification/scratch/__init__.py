import frappe
import sys
import importlib

def debug_worker_import():
    """Check if the module can be imported in worker context"""
    frappe.init("kreativ316")
    frappe.connect()
    
    print("=== sys.path (kreativ related) ===")
    for p in sys.path:
        if 'kreativ' in p or 'bench-v16/apps' in p:
            print(f"  {p}")
    
    print("\n=== Import test ===")
    tests = [
        "kreativ_notification",
        "kreativ_notification.kreativ_notification",
        "kreativ_notification.kreativ_notification.kreativ_notification",
        "kreativ_notification.kreativ_notification.kreativ_notification.doctype",
        "kreativ_notification.kreativ_notification.kreativ_notification.doctype.notification_channel",
        "kreativ_notification.kreativ_notification.kreativ_notification.doctype.notification_channel.notification_channel",
    ]
    for mod in tests:
        try:
            m = importlib.import_module(mod)
            print(f"  OK: {mod} -> {getattr(m, '__file__', 'namespace')}")
        except ImportError as e:
            print(f"  FAIL: {mod} -> {e}")
    
    # Check what get_module_name constructs
    from kreativ_notification.kreativ_notification.notification.dispatcher import dispatch
    from frappe.modules.utils import get_module_name, get_module_app, scrub
    module_name = get_module_name("notification_channel", "kreativ_notification", app="kreativ_notification")
    print(f"\n=== Frappe get_module_name: {module_name} ===")
    try:
        m = importlib.import_module(module_name)
        print(f"  OK -> {m.__file__}")
    except ImportError as e:
        print(f"  FAIL -> {e}")
    
    # Now check what frappe.get_module does
    try:
        m = frappe.get_module(module_name)
        print(f"  frappe.get_module OK -> {m.__file__}")
    except Exception as e:
        print(f"  frappe.get_module FAIL -> {e}")
    
    frappe.destroy()
