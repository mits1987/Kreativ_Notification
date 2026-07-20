"""High-level WhatsApp send API used by dashboard, print preview, and inbound bot.

v2 — CONSOLIDATED. The public function signatures are UNCHANGED (so
dashboard_senders.py, api.py, print_whatsapp_v4.js call-paths and
inbound.py keep working without edits), but every function now routes
through dispatcher.dispatch() instead of the legacy
openwa_client.enqueue_whatsapp_send() path.

What callers gain for free:
    - outbox Send Log row created BEFORE the network call
    - retry with backoff + terminal Permanently Failed
    - the ONE shared circuit breaker / rate limit / quiet hours
    - idempotency (when the caller passes a stable source doc)

Return shape is kept compatible: {"success": bool, "status": ...,
"log_name": ..., "error": ...}.
"""

from __future__ import annotations

import frappe

from kreativ_notification.notification.dispatcher import dispatch


def _default_chat(settings=None) -> str | None:
    settings = settings or frappe.get_cached_doc("OpenWA Settings")
    return settings.chat_id or None


def send_document_via_whatsapp(
    base64_pdf: str,
    filename: str,
    caption: str,
    chat_id_override: str = None,
    source_doctype: str = "System",
    source_docname: str = "",
    source_print_format: str = "",
) -> dict:
    """Send a PDF/document via WhatsApp (pre-rendered, base64)."""
    chat_id = chat_id_override or _default_chat()
    if not chat_id:
        return {"success": False,
                "error": "No recipient: pass chat_id_override or set the "
                         "default Chat ID in OpenWA Settings."}

    return dispatch(
        recipient=chat_id,
        text=caption or filename,
        file_b64=base64_pdf,
        filename=filename,
        mimetype="application/pdf",
        message_type="Print PDF",
        source_doctype=source_doctype,
        source_docname=source_docname,
        source_print_format=source_print_format,
        priority="Normal",
    )


def send_image_via_whatsapp(
    image_b64: str,
    filename: str,
    caption: str,
    chat_id_override: str = None,
    source_doctype: str = "System",
    source_docname: str = "",
) -> dict:
    """Send an image/screenshot via WhatsApp (pre-rendered, base64)."""
    chat_id = chat_id_override or _default_chat()
    if not chat_id:
        return {"success": False,
                "error": "No recipient: pass chat_id_override or set the "
                         "default Chat ID in OpenWA Settings."}

    return dispatch(
        recipient=chat_id,
        text=caption or filename,
        file_b64=image_b64,
        filename=filename,
        mimetype="image/png",
        message_type="Screenshot",
        source_doctype=source_doctype,
        source_docname=source_docname,
        priority="Normal",
    )


def send_text_via_whatsapp(
    text: str,
    chat_id_override: str = None,
    source_doctype: str = "System",
    source_docname: str = "",
) -> dict:
    """Send a plain text message via WhatsApp."""
    chat_id = chat_id_override or _default_chat()
    if not chat_id:
        return {"success": False,
                "error": "No recipient: pass chat_id_override or set the "
                         "default Chat ID in OpenWA Settings."}

    return dispatch(
        recipient=chat_id,
        text=text,
        message_type="Custom Text",
        source_doctype=source_doctype,
        source_docname=source_docname,
        priority="Normal",
    )


def send_test_message() -> dict:
    """Send a test message to the admin chat (OpenWA Settings > Chat ID)."""
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.chat_id:
        return {"success": False,
                "error": "No Recipient Chat ID in OpenWA Settings."}

    return dispatch(
        recipient=settings.chat_id,
        text="✅ Test message from ERPNext — your WhatsApp channel is working.",
        message_type="Test",
        source_doctype="System",
        source_docname="test",
        priority="Urgent",  # bypasses quiet hours: a test should send NOW
    )