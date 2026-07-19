"""High-level WhatsApp send API used by dashboard, print preview, and inbound bot."""
import frappe
import base64
from frappe.utils import get_url

from kreativ_notification.notification.openwa_client import (
    enqueue_whatsapp_send,
    check_circuit_breaker,
)
from kreativ_notification.notification.send_log import create_log as _create_log


def send_document_via_whatsapp(
    base64_pdf: str,
    filename: str,
    caption: str,
    chat_id_override: str = None,
    source_doctype: str = "System",
    source_docname: str = "",
    source_print_format: str = "",
) -> dict:
    """Send a PDF/document via WhatsApp."""
    try:
        check_circuit_breaker()
    except frappe.exceptions.ValidationError as e:
        return {"success": False, "error": str(e), "status": "circuit_open"}

    settings = frappe.get_cached_doc("OpenWA Settings")
    chat_id = chat_id_override or settings.chat_id

    log_name = _create_log(
        source_doctype=source_doctype,
        source_docname=source_docname,
        recipient=chat_id,
        message_type="Print PDF",
        recipient_display=chat_id,
        source_print_format=source_print_format,
        meta={"filename": filename, "caption": caption},
    )

    result = enqueue_whatsapp_send(
        action_type="send_pdf",
        log_name=log_name,
        doctype=source_doctype,
        name=source_docname,
        print_format=source_print_format,
        chat_id=chat_id,
    )
    return result


def send_image_via_whatsapp(
    image_b64: str,
    filename: str,
    caption: str,
    chat_id_override: str = None,
    source_doctype: str = "System",
    source_docname: str = "",
) -> dict:
    """Send an image/screenshot via WhatsApp."""
    try:
        check_circuit_breaker()
    except frappe.exceptions.ValidationError as e:
        return {"success": False, "error": str(e), "status": "circuit_open"}

    settings = frappe.get_cached_doc("OpenWA Settings")
    chat_id = chat_id_override or settings.chat_id

    log_name = _create_log(
        source_doctype=source_doctype,
        source_docname=source_docname,
        recipient=chat_id,
        message_type="Screenshot",
        recipient_display=chat_id,
        meta={"filename": filename, "caption": caption},
    )

    result = enqueue_whatsapp_send(
        action_type="send_screenshot",
        log_name=log_name,
        html="",  # not used for pre-rendered images
        filename=filename,
        caption=caption,
        chat_id=chat_id,
    )
    return result


def send_text_via_whatsapp(
    text: str,
    chat_id_override: str = None,
    source_doctype: str = "System",
    source_docname: str = "",
) -> dict:
    """Send a text message via WhatsApp."""
    try:
        check_circuit_breaker()
    except frappe.exceptions.ValidationError as e:
        return {"success": False, "error": str(e), "status": "circuit_open"}

    settings = frappe.get_cached_doc("OpenWA Settings")
    chat_id = chat_id_override or settings.chat_id

    log_name = _create_log(
        source_doctype=source_doctype,
        source_docname=source_docname,
        recipient=chat_id,
        message_type="Custom Text",
        recipient_display=chat_id,
        meta={"text": text},
    )

    result = enqueue_whatsapp_send(
        action_type="send_manual",
        log_name=log_name,
        message_type="Custom",
        text=text,
        chat_id_override=chat_id,
    )
    return result


def send_test_message() -> dict:
    """Send a test message to the admin chat."""
    try:
        check_circuit_breaker()
    except frappe.exceptions.ValidationError as e:
        return {"success": False, "error": str(e), "status": "circuit_open"}

    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.chat_id:
        return {"success": False, "error": "No Recipient Chat ID in OpenWA Settings."}

    log_name = _create_log(
        source_doctype="System",
        source_docname="",
        recipient=settings.chat_id,
        message_type="Test",
        meta={"type": "test"},
    )

    result = enqueue_whatsapp_send(
        action_type="send_test",
        log_name=log_name,
    )
    return result