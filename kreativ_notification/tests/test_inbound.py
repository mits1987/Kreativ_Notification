"""Tests for inbound.py - webhook signature verification and HTTP status codes."""
import json
import unittest
from unittest.mock import Mock, patch
import hmac
import hashlib
from frappe.tests import IntegrationTestCase
import frappe


class TestInboundWebhook(IntegrationTestCase):
    """Tests for webhook signature verification."""

    def test_valid_signature(self):
        """Correct HMAC-SHA256 signature passes."""
        from kreativ_notification.notification.security import verify_webhook_signature

        secret = "test-secret-123"
        payload = b'{"message": "hello"}'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        result = verify_webhook_signature(payload, sig, secret)
        self.assertTrue(result)

    def test_invalid_signature_rejected(self):
        """Wrong signature fails verification."""
        from kreativ_notification.notification.security import verify_webhook_signature

        secret = "test-secret-123"
        payload = b'{"message": "hello"}'
        bad_sig = hmac.new(b"wrong-secret", payload, hashlib.sha256).hexdigest()

        result = verify_webhook_signature(payload, bad_sig, secret)
        self.assertFalse(result)

    def test_missing_header_rejected(self):
        """Missing signature fails."""
        from kreativ_notification.notification.security import verify_webhook_signature

        result = verify_webhook_signature(b'{}', "", "secret")
        self.assertFalse(result)

    def test_empty_body_rejected(self):
        """Empty body fails."""
        from kreativ_notification.notification.security import verify_webhook_signature

        result = verify_webhook_signature(b"", "sig", "secret")
        self.assertFalse(result)


class TestInboundHelpers(IntegrationTestCase):
    """Tests for security.py helpers."""

    def test_respond_returns_dict(self):
        """_respond returns body dict and sets http_status_code."""
        from kreativ_notification.notification.security import _respond

        frappe.local.response = {}
        body, code = _respond("OK", 200)
        self.assertEqual(body, "OK")
        self.assertEqual(frappe.local.response["http_status_code"], 200)

    def test_respond_default_200(self):
        """Default status is 200."""
        from kreativ_notification.notification.security import _respond

        frappe.local.response = {}
        body, code = _respond("Accepted")
        self.assertEqual(frappe.local.response["http_status_code"], 200)

    def test_set_http_status(self):
        """set_http_status sets response code."""
        from kreativ_notification.notification.security import set_http_status

        frappe.local.response = {}
        set_http_status(401)
        self.assertEqual(frappe.local.response["http_status_code"], 401)


class TestReceiveMessageEndpoint(IntegrationTestCase):
    """Test the public webhook endpoint."""

    def setUp(self):
        super().setUp()
        frappe.db.set_single_value("OpenWA Settings", "webhook_secret", "correct-secret")
        frappe.db.set_single_value("OpenWA Settings", "webhook_enabled", 1)
        frappe.db.commit()

    @patch("kreativ_notification.notification.inbound.frappe.request")
    def test_signature_failure_returns_401(self, mock_request):
        """Invalid signature -> 401."""
        from kreativ_notification.notification.inbound import receive_whatsapp_message

        payload = {"type": "message", "session": "default", "message": {"id": "1", "from": "x", "body": "x"}}
        mock_request.get_data.return_value = json.dumps(payload).encode()
        mock_request.headers = {"X-OpenWA-Signature": hmac.new(b"wrong", json.dumps(payload).encode(), hashlib.sha256).hexdigest()}

        response = receive_whatsapp_message()
        self.assertEqual(frappe.local.response["http_status_code"], 401)
        self.assertIn("error", response.get("message", ""))

    @patch("kreativ_notification.notification.inbound.frappe.request")
    def test_empty_payload_returns_400(self, mock_request):
        """Empty body -> 400."""
        from kreativ_notification.notification.inbound import receive_whatsapp_message

        mock_request.get_data.return_value = b""
        mock_request.headers = {"X-OpenWA-Signature": "sig"}

        response = receive_whatsapp_message()
        self.assertEqual(frappe.local.response["http_status_code"], 400)

    @patch("kreativ_notification.notification.inbound._handle_text_message")
    @patch("kreativ_notification.notification.inbound.frappe.request")
    def test_valid_message_returns_202(self, mock_request, mock_handle):
        """Valid message -> 202 Accepted."""
        from kreativ_notification.notification.inbound import receive_whatsapp_message

        payload = {
            "type": "message",
            "session": "default",
            "message": {"id": "1", "from": "919999999999@c.us", "body": "ping"}
        }
        mock_request.get_data.return_value = json.dumps(payload).encode()
        mock_request.headers = {"X-OpenWA-Signature": hmac.new(b"correct-secret", json.dumps(payload).encode(), hashlib.sha256).hexdigest()}

        response = receive_whatsapp_message()
        self.assertEqual(frappe.local.response["http_status_code"], 202)


if __name__ == "__main__":
    unittest.main()