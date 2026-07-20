"""WhatsApp Send Log helper — delegates to the DocType owned by gravures_custom."""
import frappe
from frappe.utils import now_datetime


def create_log(
    source_doctype: str,
    source_docname: str,
    recipient: str,
    message_type: str = "Print PDF",
    recipient_display: str = "",
    source_print_format: str = "",
    meta: dict = None,
) -> str:
    """Create a WhatsApp Send Log entry and return its name."""
    # "System" is not a valid DocType, use "DocType" for test/system messages
    if source_doctype == "System":
        source_doctype = "DocType"
    try:
        log = frappe.get_doc({
            "doctype": "WhatsApp Send Log",
            "source_doctype": source_doctype,
            "source_docname": source_docname,
            "recipient": recipient,
            "recipient_display": recipient_display or "",
            "message_type": message_type,
            "source_print_format": source_print_format or "",
            "status": "Queued",
            "sent_by": frappe.session.user,
            "sent_at": now_datetime(),
            "meta": frappe.as_json(meta or {}),
        })
        log.insert(ignore_permissions=True)
        frappe.db.commit()
        return log.name
    except Exception:
        frappe.log_error(
            title="WhatsApp Send Log creation failed",
            message=frappe.get_traceback(),
        )
        return None


def update_log_status(log_name: str, status: str, error_message: str = None):
    """Update WhatsApp Send Log with result."""
    if not log_name:
        return
    try:
        frappe.db.set_value("WhatsApp Send Log", log_name, {
            "status": status,
            "error_message": error_message or "",
        }, update_modified=False)
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            title="WhatsApp Send Log update failed",
            message=frappe.get_traceback(),
        )
