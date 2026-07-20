"""OpenWA (self-hosted, unofficial WhatsApp) driver.

Credentials come from the Notification Channel document. For backwards
compatibility, blank fields fall back to the legacy OpenWA Settings single.
"""

from __future__ import annotations

import re

import frappe
import requests

from kreativ_notification.notification.channels.base import BaseChannelDriver, SendResult

REQUEST_TIMEOUT = 30


class OpenWADriver(BaseChannelDriver):
    driver_type = "WhatsApp - OpenWA"
    supports_documents = True
    supports_images = True
    supports_templates = False

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _config(self) -> tuple[str, str, str]:
        base_url = (self.channel.base_url or "").strip()
        api_key = self.channel.get_password("api_key", raise_exception=False) or ""
        session_id = (self.channel.session_id or "default").strip()

        # Legacy fallback so existing sites keep working mid-migration
        if not base_url and frappe.db.exists("DocType", "OpenWA Settings"):
            legacy = frappe.get_cached_doc("OpenWA Settings")
            base_url = (legacy.base_url or "").strip()
            api_key = legacy.get_password("api_key", raise_exception=False) or api_key
            session_id = (legacy.session_id or session_id).strip()

        return base_url.rstrip("/"), api_key, session_id

    def _post(self, endpoint: str, payload: dict) -> SendResult:
        base_url, api_key, session_id = self._config()
        if not base_url:
            return SendResult.fail("OpenWA Base URL is not configured.", permanent=True)

        url = f"{base_url}/api/sessions/{session_id}/messages/{endpoint}"
        try:
            r = requests.post(url, json=payload,
                              headers={"X-API-Key": api_key},
                              timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            return SendResult.fail(f"OpenWA unreachable: {e}")

        if not r.ok:
            return SendResult.fail(f"OpenWA HTTP {r.status_code}: {r.text[:300]}")

        try:
            data = r.json()
        except ValueError:
            data = {}
        return SendResult.ok(message_id=(data or {}).get("messageId"), raw=data)

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send_text(self, recipient: str, text: str, **kwargs) -> SendResult:
        return self._post("send-text", {"chatId": recipient, "text": text})

    def send_document(self, recipient: str, file_b64: str, filename: str,
                      mimetype: str = "application/pdf", caption: str = "",
                      **kwargs) -> SendResult:
        return self._post("send-document", {
            "chatId": recipient,
            "base64": file_b64,
            "filename": filename,
            "mimetype": mimetype,
            "caption": caption,
        })

    def send_image(self, recipient: str, image_b64: str, filename: str,
                   caption: str = "", **kwargs) -> SendResult:
        return self._post("send-image", {
            "chatId": recipient,
            "base64": image_b64,
            "filename": filename,
            "caption": caption,
        })

    # ------------------------------------------------------------------
    # Recipient normalisation: mobile number → chat_id
    # ------------------------------------------------------------------

    def normalize_recipient(self, raw: str) -> str | None:
        raw = (raw or "").strip()
        if not raw:
            return None
        if raw.endswith("@c.us") or raw.endswith("@g.us"):
            return raw

        digits = re.sub(r"\D", "", raw)
        if len(digits) < 8:
            return None

        country_code = re.sub(r"\D", "", self.channel.default_country_code or "91")
        # 10-digit local number → prefix country code
        if len(digits) == 10:
            digits = country_code + digits
        return f"{digits}@c.us"

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def get_health(self) -> dict:
        base_url, api_key, session_id = self._config()
        if not base_url:
            return {"healthy": False, "status": "unconfigured", "detail": "No base URL"}
        try:
            r = requests.get(f"{base_url}/api/sessions/{session_id}/status",
                             headers={"X-API-Key": api_key}, timeout=10)
            data = r.json() if r.ok else {}
            status = (data.get("status") or "unknown").lower()
            return {
                "healthy": status in ("ready", "connected"),
                "status": status,
                "detail": "",
            }
        except Exception as e:
            return {"healthy": False, "status": "error", "detail": str(e)}
