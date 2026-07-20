"""Email driver — routes through Frappe's Email Queue.

Exists so email is a first-class channel in rules and fallback chains
(e.g. WhatsApp undelivered after 30 min → email).
"""

from __future__ import annotations

import base64
import re

import frappe

from kreativ_notification.notification.channels.base import BaseChannelDriver, SendResult

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailDriver(BaseChannelDriver):
    driver_type = "Email"
    supports_documents = True
    supports_images = True
    supports_templates = False

    def send_text(self, recipient: str, text: str, **kwargs) -> SendResult:
        subject = kwargs.get("subject") or (text.splitlines()[0][:80] if text else "Notification")
        try:
            frappe.sendmail(
                recipients=[recipient],
                subject=subject,
                message=text.replace("\n", "<br>"),
                delayed=True,
            )
            return SendResult.ok()
        except Exception as e:
            return SendResult.fail(str(e))

    def send_document(self, recipient: str, file_b64: str, filename: str,
                      mimetype: str = "application/pdf", caption: str = "",
                      **kwargs) -> SendResult:
        subject = kwargs.get("subject") or caption or filename
        try:
            frappe.sendmail(
                recipients=[recipient],
                subject=subject,
                message=(caption or subject).replace("\n", "<br>"),
                attachments=[{"fname": filename,
                              "fcontent": base64.b64decode(file_b64)}],
                delayed=True,
            )
            return SendResult.ok()
        except Exception as e:
            return SendResult.fail(str(e))

    def normalize_recipient(self, raw: str) -> str | None:
        raw = (raw or "").strip()
        return raw if EMAIL_RE.match(raw) else None

    def get_health(self) -> dict:
        has_account = bool(frappe.get_all(
            "Email Account", filters={"enable_outgoing": 1}, limit_page_length=1))
        return {
            "healthy": has_account,
            "status": "connected" if has_account else "unconfigured",
            "detail": "" if has_account else "No outgoing Email Account enabled",
        }
