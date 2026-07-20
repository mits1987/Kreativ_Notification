"""Base channel driver interface.

Every provider (OpenWA, Meta Cloud API, Email, SMS...) implements this
interface. The dispatcher only ever talks to a BaseChannelDriver, so
adding a new provider never touches the pipeline.

Contract:
    - All send_* methods return a dict: {"success": bool, "message_id": str|None,
      "error": str|None, "raw": dict}
    - Drivers must NEVER raise for delivery failures — return success=False.
      Raise only for programming errors (bad arguments).
    - Drivers must not write to the Send Log or touch the circuit breaker;
      the dispatcher owns state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SendResult(dict):
    """Thin dict subclass so drivers can `return SendResult.ok(...)`."""

    @classmethod
    def ok(cls, message_id: str | None = None, raw: dict | None = None) -> "SendResult":
        return cls(success=True, message_id=message_id, error=None, raw=raw or {})

    @classmethod
    def fail(cls, error: str, raw: dict | None = None, permanent: bool = False) -> "SendResult":
        """permanent=True → do not retry (invalid number, unregistered template...)."""
        return cls(success=False, message_id=None, error=error, raw=raw or {}, permanent=permanent)


class BaseChannelDriver(ABC):
    """Abstract provider driver.

    Instantiated with the `Notification Channel` document that holds
    credentials and limits for one configured channel.
    """

    #: machine name used in the Notification Channel `channel_type` field
    driver_type: str = None
    #: capabilities — dispatcher checks these before routing
    supports_documents: bool = True
    supports_images: bool = True
    supports_templates: bool = False  # provider-side approved templates (Meta)

    def __init__(self, channel_doc):
        self.channel = channel_doc

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    @abstractmethod
    def send_text(self, recipient: str, text: str, **kwargs) -> SendResult:
        """Send a plain text message."""

    @abstractmethod
    def send_document(self, recipient: str, file_b64: str, filename: str,
                      mimetype: str = "application/pdf", caption: str = "",
                      **kwargs) -> SendResult:
        """Send a document attachment (base64 encoded)."""

    def send_image(self, recipient: str, image_b64: str, filename: str,
                   caption: str = "", **kwargs) -> SendResult:
        """Send an image. Default: fall back to send_document."""
        return self.send_document(recipient, image_b64, filename,
                                  mimetype="image/png", caption=caption, **kwargs)

    def send_template(self, recipient: str, template_name: str, language: str,
                      components: list | None = None, **kwargs) -> SendResult:
        """Send a provider-side approved template (Meta Cloud API).

        Drivers that don't support provider templates keep the default,
        and the dispatcher renders the body locally and uses send_text.
        """
        return SendResult.fail(
            f"{self.driver_type} does not support provider-side templates",
            permanent=True,
        )

    # ------------------------------------------------------------------
    # Recipient normalisation
    # ------------------------------------------------------------------

    def normalize_recipient(self, raw: str) -> str | None:
        """Convert a stored mobile/email into this provider's address format.

        Return None if the recipient is unusable (→ permanent failure).
        Default implementation returns the value unchanged.
        """
        return (raw or "").strip() or None

    # ------------------------------------------------------------------
    # Health / session
    # ------------------------------------------------------------------

    def get_health(self) -> dict:
        """Return {"healthy": bool, "status": str, "detail": str}."""
        return {"healthy": True, "status": "unknown", "detail": ""}

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def parse_inbound(self, headers: dict, payload: bytes) -> dict | None:
        """Parse a provider webhook into the canonical inbound event:

            {"kind": "message", "chat_id": ..., "text": ..., "raw": {...}}
            {"kind": "status",  "message_id": ..., "status": "delivered|read|failed", "raw": {...}}

        Return None if the payload is not relevant. Drivers that don't
        receive webhooks can keep the default.
        """
        return None

    def verify_inbound_signature(self, headers: dict, payload: bytes) -> bool:
        """Verify webhook authenticity. Default: reject nothing extra."""
        return True
