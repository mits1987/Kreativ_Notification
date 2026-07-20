"""Meta WhatsApp Cloud API driver (official Business API).

Uses the Graph API. Requires on the Notification Channel:
    - phone_number_id
    - access_token (permanent System User token recommended)
    - business_account_id (for template sync, optional)

Notes on Meta's rules:
    - Business-initiated messages OUTSIDE the 24h customer-service window
      MUST use an approved template (send_template).
    - Free-form send_text/send_document work only inside the 24h window
      after the customer last messaged you.
The dispatcher decides which to use via Message Template.meta_template_name.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re

import frappe
import requests

from kreativ_notification.notification.channels.base import BaseChannelDriver, SendResult

GRAPH_VERSION = "v21.0"
REQUEST_TIMEOUT = 30

# Graph error codes that mean "do not retry"
PERMANENT_ERROR_CODES = {
    131026,  # message undeliverable / recipient not on WhatsApp
    131047,  # re-engagement required (outside 24h window, no template)
    132000,  # template does not exist
    132001,  # template not approved
    100,     # invalid parameter
}


class MetaCloudDriver(BaseChannelDriver):
    driver_type = "WhatsApp - Meta Cloud API"
    supports_documents = True
    supports_images = True
    supports_templates = True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _base_url(self) -> str:
        return f"https://graph.facebook.com/{GRAPH_VERSION}/{self.channel.phone_number_id}"

    def _headers(self) -> dict:
        token = self.channel.get_password("access_token", raise_exception=False) or ""
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _post_messages(self, payload: dict) -> SendResult:
        if not self.channel.phone_number_id:
            return SendResult.fail("phone_number_id not configured.", permanent=True)
        try:
            r = requests.post(f"{self._base_url()}/messages",
                              headers=self._headers(),
                              json=payload, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            return SendResult.fail(f"Meta Cloud API unreachable: {e}")

        try:
            data = r.json()
        except ValueError:
            data = {}

        if r.ok:
            messages = data.get("messages") or [{}]
            return SendResult.ok(message_id=messages[0].get("id"), raw=data)

        err = (data.get("error") or {})
        code = err.get("code")
        detail = err.get("message", f"HTTP {r.status_code}")
        return SendResult.fail(
            f"Meta error {code}: {detail}",
            raw=data,
            permanent=code in PERMANENT_ERROR_CODES,
        )

    def _upload_media(self, file_b64: str, filename: str, mimetype: str) -> str | None:
        """Upload media, return media id (Meta requires upload-then-reference)."""
        try:
            file_bytes = base64.b64decode(file_b64)
            r = requests.post(
                f"{self._base_url()}/media",
                headers={"Authorization": self._headers()["Authorization"]},
                data={"messaging_product": "whatsapp"},
                files={"file": (filename, file_bytes, mimetype)},
                timeout=60,
            )
            if r.ok:
                return r.json().get("id")
            frappe.log_error(title="Meta media upload failed", message=r.text[:500])
        except Exception:
            frappe.log_error(title="Meta media upload error",
                             message=frappe.get_traceback())
        return None

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send_text(self, recipient: str, text: str, **kwargs) -> SendResult:
        return self._post_messages({
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {"body": text, "preview_url": False},
        })

    def send_document(self, recipient: str, file_b64: str, filename: str,
                      mimetype: str = "application/pdf", caption: str = "",
                      **kwargs) -> SendResult:
        media_id = self._upload_media(file_b64, filename, mimetype)
        if not media_id:
            return SendResult.fail("Media upload to Meta failed.")
        return self._post_messages({
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "document",
            "document": {"id": media_id, "filename": filename, "caption": caption},
        })

    def send_image(self, recipient: str, image_b64: str, filename: str,
                   caption: str = "", **kwargs) -> SendResult:
        media_id = self._upload_media(image_b64, filename, "image/png")
        if not media_id:
            return SendResult.fail("Media upload to Meta failed.")
        return self._post_messages({
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "image",
            "image": {"id": media_id, "caption": caption},
        })

    def send_template(self, recipient: str, template_name: str, language: str,
                      components: list | None = None, **kwargs) -> SendResult:
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language or "en"},
            },
        }
        if components:
            payload["template"]["components"] = components
        return self._post_messages(payload)

    # ------------------------------------------------------------------
    # Recipient normalisation: E.164 digits, no @c.us suffix
    # ------------------------------------------------------------------

    def normalize_recipient(self, raw: str) -> str | None:
        raw = (raw or "").strip()
        if not raw:
            return None
        # Strip OpenWA-style suffix if migrating data
        raw = raw.replace("@c.us", "")
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 8:
            return None
        country_code = re.sub(r"\D", "", self.channel.default_country_code or "91")
        if len(digits) == 10:
            digits = country_code + digits
        return digits

    # ------------------------------------------------------------------
    # Health — verify token by fetching the phone number object
    # ------------------------------------------------------------------

    def get_health(self) -> dict:
        if not (self.channel.phone_number_id and
                self.channel.get_password("access_token", raise_exception=False)):
            return {"healthy": False, "status": "unconfigured",
                    "detail": "phone_number_id / access_token missing"}
        try:
            r = requests.get(self._base_url(), headers=self._headers(), timeout=10)
            if r.ok:
                return {"healthy": True, "status": "connected", "detail": ""}
            return {"healthy": False, "status": "auth_error", "detail": r.text[:200]}
        except Exception as e:
            return {"healthy": False, "status": "error", "detail": str(e)}

    # ------------------------------------------------------------------
    # Inbound (webhook) — messages + delivery statuses
    # ------------------------------------------------------------------

    def verify_inbound_signature(self, headers: dict, payload: bytes) -> bool:
        secret = self.channel.get_password("webhook_secret", raise_exception=False)
        if not secret:
            return True  # not configured → cannot verify; channel owner's choice
        signature = headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(secret.encode(), payload,
                                        hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_inbound(self, headers: dict, payload: bytes) -> dict | None:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                # Delivery / read receipts
                for status in value.get("statuses", []):
                    return {
                        "kind": "status",
                        "message_id": status.get("id"),
                        "status": status.get("status"),  # sent|delivered|read|failed
                        "raw": status,
                    }

                # Inbound customer messages
                for msg in value.get("messages", []):
                    text = ""
                    if msg.get("type") == "text":
                        text = (msg.get("text") or {}).get("body", "")
                    return {
                        "kind": "message",
                        "chat_id": msg.get("from"),
                        "text": text,
                        "message_id": msg.get("id"),
                        "raw": msg,
                    }
        return None
