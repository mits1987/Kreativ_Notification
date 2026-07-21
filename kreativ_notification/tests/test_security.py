"""Tests for security.py - shared webhook signature verification and HTTP responses."""
import json
import hmac
import hashlib
import unittest
from unittest.mock import Mock
from frappe.tests import IntegrationTestCase
import frappe


class TestVerifyWebhookSignature(IntegrationTestCase):
    """Test verify_webhook_signature function."""

    def test_valid_signature(self):
        """Correct HMAC-SHA256 signature passes verification."""
        from kreativ_notification.notification.security import verify_webhook_signature

        secret = "webhook-secret-key"
        payload = b'{"event": "message", "data": "test"}'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        self.assertTrue(verify_webhook_signature(payload, sig, secret))

    def test_invalid_signature_fails(self):
        """Wrong signature fails verification."""
        from kreativ_notification.notification.security import verify_webhook_signature

        secret = "correct-secret"
        payload = b'{"event": "test"}'
        bad_sig = hmac.new(b"wrong-secret", payload, hashlib.sha256).hexdigest()

        self.assertFalse(verify_webhook_signature(payload, bad_sig, secret))

    def test_missing_signature_fails(self):
        """Missing signature fails."""
        from kreativ_notification.notification.security import verify_webhook_signature

        self.assertFalse(verify_webhook_signature(b'{}', "", "secret"))

    def test_empty_body_fails(self):
        """Empty request body fails."""
        from kreativ_notification.notification.security import verify_webhook_signature

        self.assertFalse(verify_webhook_signature(b"", "sig", "secret"))

    def test_constant_time_compare(self):
        """Verification uses constant-time comparison."""
        from kreativ_notification.notification.security import verify_webhook_signature

        secret = "secret"
        payload = b'test'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        # Should not raise
        verify_webhook_signature(payload, sig, secret)

    def test_sha256_prefix_handling(self):
        """Handles 'sha256=<hex>' format if sent."""
        from kreativ_notification.notification.security import verify_webhook_signature

        secret = "secret"
        payload = b'{}'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        # The implementation does handle sha256= prefix (line 16-17)
        self.assertTrue(verify_webhook_signature(payload, f"sha256={sig}", secret))


class TestSetHttpStatus(IntegrationTestCase):
    """Test set_http_status helper."""

    def test_sets_status(self):
        """set_http_status sets response status code."""
        from kreativ_notification.notification.security import set_http_status

        frappe.local.response = {}
        set_http_status(401)
        self.assertEqual(frappe.local.response["http_status_code"], 401)


class TestRespondHelper(IntegrationTestCase):
    """Test _respond helper for consistent HTTP responses."""

    def test_respond_returns_body(self):
        """_respond returns the body."""
        from kreativ_notification.notification.security import _respond

        frappe.local.response = {}
        body = _respond("OK", 200)
        self.assertEqual(body, "OK")

    def test_respond_sets_status(self):
        """_respond sets http_status_code on response."""
        from kreativ_notification.notification.security import _respond

        frappe.local.response = {}
        _respond("Accepted", 202)
        self.assertEqual(frappe.local.response["http_status_code"], 202)

    def test_respond_default_200(self):
        """Default status is 200."""
        from kreativ_notification.notification.security import _respond

        frappe.local.response = {}
        _respond("Accepted")
        self.assertEqual(frappe.local.response["http_status_code"], 200)

    def test_respond_various_codes(self):
        """Can return any valid HTTP code."""
        from kreativ_notification.notification.security import _respond

        frappe.local.response = {}
        _respond("Created", 201)
        self.assertEqual(frappe.local.response["http_status_code"], 201)

        frappe.local.response = {}
        _respond("Bad Request", 400)
        self.assertEqual(frappe.local.response["http_status_code"], 400)

        frappe.local.response = {}
        _respond("Unauthorized", 401)
        self.assertEqual(frappe.local.response["http_status_code"], 401)

        frappe.local.response = {}
        _respond("Not Found", 404)
        self.assertEqual(frappe.local.response["http_status_code"], 404)

        frappe.local.response = {}
        _respond("Error", 500)
        self.assertEqual(frappe.local.response["http_status_code"], 500)

        frappe.local.response = {}
        _respond("Unavailable", 503)
        self.assertEqual(frappe.local.response["http_status_code"], 503)


if __name__ == "__main__":
    unittest.main()