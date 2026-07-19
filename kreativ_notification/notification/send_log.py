"""WhatsApp Send Log helper — thin wrapper around the DocType."""
import frappe


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
    try:
        from kreativ_notification.kreativ_notification.doctype.whatsapp_send_log.whatsapp_send_log import (
            create_log as _create,
        )
        return _create(
            source_doctype=source_doctype,
            source_docname=source_docname,
            recipient=recipient,
            message_type=message_type,
            recipient_display=recipient_display,
            source_print_format=source_print_format,
            meta=meta or {},
        )
    except Exception:
        frappe.log_error(
            title="WhatsApp Send Log creation failed",
            message=frappe.get_traceback(),
        )
        return None


def update_log_status(log_name: str, success: bool, error_message: str = ""):
    """Update WhatsApp Send Log with result."""
    if not log_name:
        return
    try:
        from kreativ_notification.kreativ_notification.doctype.whatsapp_send_log.whatsapp_send_log import (
            update_log_status as _update,
        )
        _update(log_name, "Sent" if success else "Failed", error_message or None)
    except Exception:
        frappe.log_error(
            title="WhatsApp Send Log update failed",
            message=frappe.get_traceback(),
        )