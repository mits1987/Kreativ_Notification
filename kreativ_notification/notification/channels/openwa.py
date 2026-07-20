"""OpenWA (self-hosted, unofficial WhatsApp) driver.

v2 — UNIFIED HTTP PATH. This driver no longer makes its own raw
requests.post() calls: all gateway HTTP goes through OpenWAClient in
notification/openwa_client.py, which is now the ONE place that knows the
OpenWA REST surface, timeouts, and error normalisation. The driver's job
shrinks to what a driver should do:

    - resolve credentials (Notification Channel, legacy Settings fallback)
    - normalise recipients (number -> chat_id)
    - translate the client's {"success", "data"/"error"} dicts into
      SendResult for the dispatcher

Per the BaseChannelDriver contract this driver NEVER raises for delivery
failures and never touches the Send Log or circuit breaker — the
dispatcher owns state. (The gateway-level breaker in openwa_client.py is
used by health checks and the inbound bot, not by dispatch delivery;
see the note at the top of openwa_client.py.)

REQUIRES the openwa_client.py __init__ patch (ADDENDUM.md §1) so
OpenWAClient accepts explicit (base_url, api_key, session_id).
"""

from __future__ import annotations

import re

import frappe

from kreativ_notification.notification.channels.base import BaseChannelDriver, SendResult
from kreativ_notification.notification.openwa_client import OpenWAClient

# Chat-id suffixes OpenWA uses; inbound reply_to may carry any of these.
CHAT_ID_SUFFIXES = ("@c.us", "@g.us", "@lid", "@s.whatsapp.net")


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

    def _client(self) -> OpenWAClient | SendResult:
        """Build a client for this channel's credentials, or a permanent
        SendResult failure if the channel is unconfigured."""
        base_url, api_key, session_id = self._config()
        if not base_url:
            return SendResult.fail("OpenWA Base URL is not configured.", permanent=True)
        if not api_key:
            return SendResult.fail("OpenWA API Key is not configured.", permanent=True)
        return OpenWAClient(base_url=base_url, api_key=api_key, session_id=session_id)

    @staticmethod
    def _to_result(res: dict) -> SendResult:
        """OpenWAClient dict -> dispatcher SendResult."""
        if res.get("success"):
            data = res.get("data") or {}
            return SendResult.ok(message_id=data.get("messageId"), raw=data)
        return SendResult.fail(res.get("error") or "OpenWA send failed", raw=res)

    def _send(self, method: str, *args, **kwargs) -> SendResult:
        client = self._client()
        if isinstance(client, SendResult):  # unconfigured -> permanent fail
            return client
        try:
            return self._to_result(getattr(client, method)(*args, **kwargs))
        except Exception as e:
            # Contract: never raise for delivery failures.
            return SendResult.fail(f"OpenWA driver error: {e}")

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send_text(self, recipient: str, text: str, **kwargs) -> SendResult:
        return self._send("send_text", recipient, text)

    def send_document(self, recipient: str, file_b64: str, filename: str,
                      mimetype: str = "application/pdf", caption: str = "",
                      **kwargs) -> SendResult:
        return self._send("send_document", recipient, file_b64, filename,
                          mimetype=mimetype, caption=caption)

    def send_image(self, recipient: str, image_b64: str, filename: str,
                   caption: str = "", **kwargs) -> SendResult:
        return self._send("send_image", recipient, image_b64, filename,
                          caption=caption)

    # ------------------------------------------------------------------
    # Recipient normalisation: mobile number -> chat_id
    # ------------------------------------------------------------------

    def normalize_recipient(self, raw: str) -> str | None:
        raw = (raw or "").strip()
        if not raw:
            return None
        # FIX v2: pass through ALL chat-id forms. The old version only
        # recognised @c.us/@g.us — an inbound reply_to like
        # "123456789@lid" was digit-stripped and rebuilt as a wrong
        # "...@c.us" address. @lid and @s.whatsapp.net are valid inbound
        # sender forms (see inbound.py's valid_suffixes) and must be
        # replied to verbatim.
        if raw.endswith(CHAT_ID_SUFFIXES):
            return raw

        digits = re.sub(r"\D", "", raw)
        if len(digits) < 8:
            return None

        country_code = re.sub(r"\D", "", self.channel.default_country_code or "91")
        # 10-digit local number -> prefix country code
        if len(digits) == 10:
            digits = country_code + digits
        return f"{digits}@c.us"

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def get_health(self) -> dict:
        client = self._client()
        if isinstance(client, SendResult):
            return {"healthy": False, "status": "unconfigured",
                    "detail": client.get("error") or "No base URL / API key"}
        try:
            status = (client.get_session_state() or {}).get("status", "unknown").lower()
            return {
                "healthy": status in ("ready", "connected"),
                "status": status,
                "detail": "",
            }
        except Exception as e:
            return {"healthy": False, "status": "error", "detail": str(e)}