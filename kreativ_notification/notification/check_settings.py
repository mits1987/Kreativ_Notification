"""Quick helper to check/fix OpenWA Settings and Notification Channel on any site."""
import frappe
import requests


def execute():
    print("=== OpenWA Settings ===")
    s = frappe.get_doc("OpenWA Settings")
    print("base_url:", s.base_url)
    print("session_id:", s.session_id)
    print("api_key:", "***" if s.get_password("api_key", raise_exception=False) else "NOT SET")

    print("\n=== Notification Channel: Primary WhatsApp ===")
    if frappe.db.exists("Notification Channel", "Primary WhatsApp"):
        ch = frappe.get_doc("Notification Channel", "Primary WhatsApp")
        print("base_url:", ch.base_url)
        print("session_id:", ch.session_id)
        print("api_key:", "***" if ch.get_password("api_key", raise_exception=False) else "NOT SET")
    else:
        print("NOT FOUND")


def fix_session(real_session_id: str = "420484e2-9a57-4368-8738-2a9e595fead0"):
    """Update session_id in both OpenWA Settings and Notification Channel."""
    frappe.db.set_value("OpenWA Settings", "OpenWA Settings", "session_id", real_session_id)
    print(f"OpenWA Settings session_id -> {real_session_id}")

    if frappe.db.exists("Notification Channel", "Primary WhatsApp"):
        frappe.db.set_value("Notification Channel", "Primary WhatsApp", "session_id", real_session_id)
        print(f"Notification Channel session_id -> {real_session_id}")

    frappe.db.commit()


def check_session():
    """Query OpenWA gateway to verify the session is alive."""
    s = frappe.get_doc("OpenWA Settings")
    base_url = s.base_url.rstrip("/")
    api_key = s.get_password("api_key", raise_exception=False)
    session_id = s.session_id

    print(f"base_url: {base_url}")
    print(f"session_id: {session_id}")
    print(f"api_key: {'SET' if api_key else 'NOT SET'}")

    try:
        r = requests.get(f"{base_url}/api/sessions/{session_id}",
                         headers={"X-API-Key": api_key}, timeout=10)
        print(f"\nGET /api/sessions/{session_id} -> {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"  status: {data.get('status')}")
            print(f"  phone: {data.get('phone')}")
            print(f"  pushname: {data.get('pushname')}")
            return data
        else:
            print(f"  response: {r.text[:300]}")
            return None
    except Exception as e:
        print(f"ERROR: {e}")
        return None
