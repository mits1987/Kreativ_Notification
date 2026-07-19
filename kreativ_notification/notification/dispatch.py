"""Dispatch helpers for WhatsApp."""
import frappe
from frappe.utils import get_url

from kreativ_notification.notification.openwa_client import get_openwa_config


def _generate_pdf_bytes(doctype: str, name: str, print_format: str = None) -> bytes:
    """Generate PDF bytes for a document."""
    return frappe.get_print(
        doctype, name,
        print_format=print_format or None,
        as_pdf=True,
    )


def _screenshot_html(html: str, width: int = 1000) -> bytes:
    """Screenshot HTML using Playwright."""
    from kreativ_notification.notification.screenshot_utils import screenshot_html_playwright
    return screenshot_html_playwright(html, width=width)


def _send_image_via_whatsapp(chat_id: str, base64_data: str, filename: str, caption: str = "") -> dict:
    """Send image via WhatsApp."""
    from kreativ_notification.notification.openwa_client import OpenWAClient
    client = OpenWAClient()
    return client.send_image(chat_id, base64_data, filename, caption)


def _send_document_via_whatsapp(chat_id: str, base64_data: str, filename: str, mimetype: str = "application/pdf", caption: str = "") -> dict:
    """Send document via WhatsApp."""
    from kreativ_notification.notification.openwa_client import OpenWAClient
    client = OpenWAClient()
    return client.send_document(chat_id, base64_data, filename, mimetype, caption)


def _dispatch_screenshot(html: str, chat_id: str, filename: str, caption: str = "") -> dict:
    """Generate screenshot and send via WhatsApp."""
    png = _screenshot_html(html)
    b64 = base64.b64encode(png).decode("utf-8")
    return _send_image_via_whatsapp(chat_id, b64, filename, caption)