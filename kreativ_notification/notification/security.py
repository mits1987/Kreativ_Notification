"""Security utilities for kreativ_notification.

Shared between inbound webhook handler and OpenWA Settings.
"""
import hmac
import hashlib


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature from webhook.

    Tolerant of both 'sha256=<hex>' and raw hex formats.
    """
    if not secret or not signature:
        return False
    if signature.startswith("sha256="):
        signature = signature[len("sha256="):]
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def set_http_status(http_status: int):
    """Set HTTP response status code for whitelisted endpoint."""
    frappe.local.response["http_status_code"] = http_status


def _respond(body: dict, http_status: int = 200) -> dict:
    """Helper to set HTTP status and return body for whitelisted endpoints."""
    frappe.local.response["http_status_code"] = http_status
    return body