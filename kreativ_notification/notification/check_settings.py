"""Quick helper to check OpenWA Settings and Notification Channel on any site."""
import frappe


def execute():
    print("=== OpenWA Settings ===")
    s = frappe.get_doc("OpenWA Settings")
    print("base_url:", s.base_url)
    print("session_id:", s.session_id)
    print("chat_id:", s.chat_id)
    print("api_key:", "***" if s.get_password("api_key", raise_exception=False) else "NOT SET")

    print("\n=== Notification Channel: Primary WhatsApp ===")
    if frappe.db.exists("Notification Channel", "Primary WhatsApp"):
        ch = frappe.get_doc("Notification Channel", "Primary WhatsApp")
        print("base_url:", ch.base_url)
        print("session_id:", ch.session_id)
        print("chat_id:", ch.chat_id)
        print("api_key:", "***" if ch.get_password("api_key", raise_exception=False) else "NOT SET")
    else:
        print("NOT FOUND")
