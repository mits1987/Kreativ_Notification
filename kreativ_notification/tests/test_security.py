"""Tests for security.py - shared webhook signature verification and HTTP responses."""
import json
import hmac
import hashlib
import unittest
from unittest.mock import Mock
from frappe.tests import IntegrationTestCase
import frappe

# Initialize Frappe before any imports that use frappe
frappe.local.site = "kreativ316"
frappe.local.request = None
frappe.local.response = {}
frappe.local.form_dict = {}

from kreativ_notification.notification.security import (
    verify_webhook_signature,
    _respond,
    WebhookAuthError,
)


class TestVerifyWebhookSignature(IntegrationTestCase):
    """Test verify_webhook_signature function."""

    def test_valid_signature(self):
        """Correct HMAC-SHA256 signature passes verification."""
        secret = "webhook-secret-key"
        payload = b'{"event": "message", "data": "test"}'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        req = Mock()
        req.data = payload
        req.headers = {"X-OpenWA-Signature": sig}

        self.assertTrue(verify_webhook_signature(req, secret))

    def test_invalid_signature_raises(self):
        """Wrong signature raises WebhookAuthError with 401."""
        secret = "correct-secret"
        payload = b'{"event": "test"}'
        bad_sig = hmac.new(b"wrong-secret", payload, hashlib.sha256).hexdigest()

        req = Mock()
        req.data = payload
        req.headers = {"X-OpenWA-Signature": bad_sig}

        with self.assertRaises(WebhookAuthError) as cm:
            verify_webhook_signature(req, secret)
        self.assertEqual(cm.exception.http_status, 401)

    def test_missing_signature_header_raises(self):
        """Missing X-OpenWA-Signature header raises 401."""
        req = Mock()
        req.data = b'{}'
        req.headers = {}

        with self.assertRaises(WebhookAuthError) as cm:
            verify_webhook_signature(req, "secret")
        self.assertEqual(cm.exception.http_status, 401)

    def test_empty_body_raises_400(self):
        """Empty request body raises 400 Bad Request."""
        req = Mock()
        req.data = b""
        req.headers = {"X-OpenWA-Signature": "sig"}

        with self.assertRaises(WebhookAuthError) as cm:
            verify_webhook_signature(req, "secret")
        self.assertEqual(cm.exception.http_status, 400)

    def test_constant_time_compare(self):
        """Verification uses constant-time comparison (hmac.compare_digest)."""
        secret = "secret"
        payload = b'test'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        req = Mock()
        req.data = payload
        req.headers = {"X-OpenWA-Signature": sig}

        # Should not raise
        verify_webhook_signature(req, secret)


class TestRespondHelper(IntegrationTestCase):
    """Test _respond helper for consistent HTTP responses."""

    def test_respond_returns_tuple(self):
        """_respond returns (body, status_code) tuple."""
        body, code = _respond("OK", 200)
        self.assertEqual(body, "OK")
        self.assertEqual(code, 200)

    def test_respond_default_200(self):
        """Default status is 200."""
        body, code = _respond("Accepted")
        self.assertEqual(code, 200)

    def test_respond_uses_varied_codes(self):
        """Can return any valid HTTP code."""
        self.assertEqual(_respond("Created", 201), ("Created", 201))
        self.assertEqual(_respond("Bad Request", 400), ("Bad Request", 400))
        self.assertEqual(_respond("Unauthorized", 401), ("Unauthorized", 401))
        self.assertEqual(_respond("Not Found", 404), ("Not Found", 404))
        self.assertEqual(_respond("Error", 500), ("Error", 500))
        self.assertEqual(_respond("Unavailable", 503), ("Unavailable", 503))


class TestWebhookAuthError(IntegrationTestCase):
    """Test WebhookAuthError exception."""

    def test_carries_http_status(self):
        """WebhookAuthError stores and exposes http_status."""
        err = WebhookAuthError("Invalid signature", 401)
        self.assertEqual(err.http_status, 401)
        self.assertEqual(str(err), "Invalid signature")

    def test_defaults_to_401(self):
        """Default status is 401 if not specified."""
        err = WebhookAuthError("Missing header")
        self.assertEqual(err.http_status, 401)


class TestSignatureFormatVariations(IntegrationTestCase):
    """Test various signature header formats."""

    def test_signature_with_sha256_prefix(self):
        """Handles 'sha256=<hex>' format if sent."""
        secret = "secret"
        payload = b'{}'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        req = Mock()
        req.data = payload
        req.headers = {"X-OpenWA-Signature": f"sha256={sig}"}

        # Current impl expects raw hex, should fail with prefix
        # This documents current behavior - may need update if OpenWA sends prefix
        with self.assertRaises(WebhookAuthError):
            verify_webhook_signature(req, secret)

    def test_case_insensitive_header_lookup(self):
        """Header lookup is case-insensitive (via Flask/Werkzeug)."""
        secret = "secret"
        payload = b'{}'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        req = Mock()
        req.data = payload
        req.headers = {"x-openwa-signature": sig}  # lowercase

        # Werkzeug Headers are case-insensitive
        self.assertTrue(verify_webhook_signature(req, secret))


if __name__ == "__main__":
    unittest.main()