"""Public Whitelisted API for WhatsApp sending.

These replace the gravures_custom.overrides whitelisted functions.
All calls are delegated to kreativ_notification.notification modules.
"""

import frappe
from frappe import _

from kreativ_notification.notification.dashboard_senders import (
    send_dispatch_summary,
    send_dispatch_register,
    send_dispatch_detail,
    send_sales_invoice_register,
    send_sales_invoice_detail,
    send_party_statement,
    send_stock_report,
    send_custom_report,
)
from kreativ_notification.notification.send import (
    send_document_via_whatsapp,
    send_image_via_whatsapp,
    send_text_via_whatsapp,
    send_test_message,
)


def _get_openwa_settings():
    """Get OpenWA Settings document."""
    return frappe.get_cached_doc("OpenWA Settings")


def _get_openwa_client():
    """Create OpenWAClient with current settings."""
    settings = _get_openwa_settings()
    return __import__(
        "kreativ_notification.notification.openwa_client",
        fromlist=["OpenWAClient"]
    ).OpenWAClient(settings.base_url, settings.api_key)


# --- Dashboard / Workspace send functions ---

@frappe.whitelist()
def send_proofing_whatsapp(from_date: str, to_date: str) -> dict:
    """Queue Proofing screenshot for WhatsApp (Proofing Area workspace button)."""
    return send_custom_report("Daily Proofing Report", {"from_date": from_date, "to_date": to_date})


@frappe.whitelist()
def send_dispatch_whatsapp(from_date: str, to_date: str) -> dict:
    """Queue Dispatch screenshot for WhatsApp (Dispatch workspace button)."""
    return send_dispatch_summary()


@frappe.whitelist()
def send_engraving_whatsapp(from_date: str, to_date: str) -> dict:
    """Queue Engraving screenshot for WhatsApp."""
    return send_custom_report("Daily Engraving Report", {"from_date": from_date, "to_date": to_date})


@frappe.whitelist()
def send_engraving_monthly_whatsapp(from_date: str, to_date: str) -> dict:
    """Queue Engraving monthly summary for WhatsApp."""
    return send_custom_report("Daily Engraving Report", {"from_date": from_date, "to_date": to_date, "monthly": True})


@frappe.whitelist()
def send_dispatch_customer_whatsapp(from_date: str, to_date: str) -> dict:
    """Queue Dispatch by Customer screenshot for WhatsApp."""
    return send_dispatch_register()


@frappe.whitelist()
def send_dispatch_monthly_whatsapp(from_date: str, to_date: str) -> dict:
    """Queue Dispatch monthly by job type for WhatsApp."""
    return send_dispatch_detail()


@frappe.whitelist()
def send_dispatch_yearly_whatsapp(from_date: str, to_date: str) -> dict:
    """Queue Dispatch yearly screenshot for WhatsApp."""
    return send_custom_report("Dispatch Yearly", {"from_date": from_date, "to_date": to_date})


@frappe.whitelist()
def send_job_status_whatsapp(from_date: str, to_date: str) -> dict:
    """Queue Sales Order Job Status screenshot for WhatsApp."""
    return send_custom_report("Job Status Report", {"from_date": from_date, "to_date": to_date})


@frappe.whitelist()
def send_monthly_report_whatsapp(from_date: str, to_date: str) -> dict:
    """Queue Kreativ Monthly Summary (proofing+engraving+dispatch) for WhatsApp."""
    return send_custom_report("Kreativ Monthly Report", {"from_date": from_date, "to_date": to_date})


# --- Print Preview WhatsApp button helpers ---

@frappe.whitelist()
def validate_phone_number(phone: str) -> dict:
    """Validate and format phone number for WhatsApp."""
    try:
        import phonenumbers
        parsed = phonenumbers.parse(phone, "IN")
        if not phonenumbers.is_valid_number(parsed):
            return {"valid": False, "error": _("Invalid phone number")}
        formatted = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        return {"valid": True, "formatted": formatted}
    except Exception as e:
        return {"valid": False, "error": str(e)}


@frappe.whitelist()
def get_whatsapp_chats(limit: int = 50) -> list:
    """Fetch recent WhatsApp chats from OpenWA for contact picker."""
    settings = _get_openwa_settings()
    if not settings.enabled:
        return []

    client = _get_openwa_client()

    try:
        resp = client.get_chats(limit=limit)
        chats = []
        for c in resp:
            chats.append({
                "chat_id": c.get("id"),
                "name": c.get("name") or c.get("pushname") or c.get("id"),
                "last_message": c.get("lastMessage", {}).get("body") if c.get("lastMessage") else "",
                "timestamp": c.get("lastMessage", {}).get("timestamp") if c.get("lastMessage") else "",
                "is_group": c.get("isGroup", False),
            })
        return chats
    except Exception as e:
        frappe.log_error(f"Failed to fetch WhatsApp chats: {e}", "WhatsApp Chats")
        return []


@frappe.whitelist()
def search_whatsapp_contacts(query: str, limit: int = 20) -> list:
    """Search WhatsApp contacts by name/number."""
    settings = _get_openwa_settings()
    if not settings.enabled:
        return []

    client = _get_openwa_client()

    try:
        chats = client.get_chats(limit=200)
        results = []
        q = query.lower()
        for c in chats:
            name = (c.get("name") or c.get("pushname") or "").lower()
            chat_id = c.get("id", "")
            if q in name or q in chat_id:
                results.append({
                    "chat_id": chat_id,
                    "name": c.get("name") or c.get("pushname") or chat_id,
                    "is_group": c.get("isGroup", False),
                })
                if len(results) >= limit:
                    break
        return results
    except Exception as e:
        frappe.log_error(f"WhatsApp contact search failed: {e}", "WhatsApp Search")
        return []


@frappe.whitelist()
def send_print_pdf_whatsapp(
    doctype: str,
    docname: str,
    print_format: str = None,
    chat_id: str = None,
    caption: str = None,
) -> dict:
    """Generate PDF from print preview and send via WhatsApp."""
    return send_document_via_whatsapp(
        source_doctype=doctype,
        source_docname=docname,
        source_print_format=print_format,
        chat_id_override=chat_id,
        caption=caption,
    )


# --- Session management ---

@frappe.whitelist()
def get_openwa_session_status() -> dict:
    """Return current OpenWA session status for admin UI."""
    settings = _get_openwa_settings()
    return {
        "enabled": settings.enabled,
        "base_url": settings.base_url,
        "session_id": settings.session_id,
        "chat_id": settings.chat_id,
        "status": "ready" if settings.session_id else "not_configured",
    }


@frappe.whitelist()
def get_openwa_session_qr() -> dict:
    """Generate QR code for new OpenWA session."""
    settings = _get_openwa_settings()
    if not settings.enabled:
        return {"success": False, "error": "WhatsApp not enabled"}

    client = _get_openwa_client()

    try:
        qr_data = client.start_session()
        return {"success": True, "qr_code": qr_data.get("qr_code")}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def start_openwa_session() -> dict:
    """Start/resume OpenWA session."""
    return get_openwa_session_qr()


@frappe.whitelist()
def stop_openwa_session() -> dict:
    """Stop current OpenWA session."""
    settings = _get_openwa_settings()
    client = _get_openwa_client()
    try:
        client.stop_session()
        settings.session_id = ""
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def send_test_whatsapp() -> dict:
    """Send a test message to the admin chat."""
    return send_test_message()