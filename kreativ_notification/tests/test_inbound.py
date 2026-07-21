"""Tests for inbound.py - webhook signature verification and HTTP status codes."""
import json
import unittest
from unittest.mock import Mock, patch
import hmac
import hashlib
from frappe.tests import IntegrationTestCase
import frappe

# Initialize Frappe before any imports that use frappe
frappe.local.site = "kreativ316"
frappe.local.request = None
frappe.local.response = {}
frappe.local.form_dict = {}

from kreativ_notification.notification.inbound import receive, _handle_text_message
from kreativ_notification.notification.security import verify_webhook_signature, _respond, WebhookAuthError
from kreativ_notification.notification.openwa_client import reset_circuit_breaker


class TestWebhookSignatureVerification(IntegrationTestCase):
    """Tests for verify_webhook_signature from security.py."""

    def test_valid_signature(self):
        """Correct HMAC-SHA256 signature passes."""
        secret = "test-secret-123"
        payload = b'{"message": "hello"}'
        # Generate correct signature
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        req = Mock()
        req.data = payload
        req.headers = {"X-OpenWA-Signature": sig}

        self.assertTrue(verify_webhook_signature(req, secret))

    def test_invalid_signature_rejected(self):
        """Wrong signature raises 401."""
        secret = "test-secret-123"
        payload = b'{"message": "hello"}'
        bad_sig = hmac.new(b"wrong-secret", payload, hashlib.sha256).hexdigest()

        req = Mock()
        req.data = payload
        req.headers = {"X-OpenWA-Signature": bad_sig}

        with self.assertRaises(WebhookAuthError) as cm:
            verify_webhook_signature(req, secret)
        self.assertEqual(cm.exception.http_status, 401)

    def test_missing_header_rejected(self):
        """Missing X-OpenWA-Signature raises 401."""
        req = Mock()
        req.data = b'{}'
        req.headers = {}

        with self.assertRaises(WebhookAuthError) as cm:
            verify_webhook_signature(req, "secret")
        self.assertEqual(cm.exception.http_status, 401)

    def test_empty_body_rejected(self):
        """Empty body raises 400."""
        req = Mock()
        req.data = b""
        req.headers = {"X-OpenWA-Signature": "test"}

        with self.assertRaises(WebhookAuthError) as cm:
            verify_webhook_signature(req, "secret")
        self.assertEqual(cm.exception.http_status, 400)


class TestInboundMessageHandler(IntegrationTestCase):
    """Tests for inbound message processing."""

    def setUp(self):
        super().setUp()
        # Create channel for signature check
        ch = frappe.get_doc({
            "doctype": "Notification Channel",
            "channel_name": "Test OpenWA",
            "channel_type": "WhatsApp - OpenWA",
            "enabled": 1,
        })
        ch.insert(ignore_permissions=True)
        frappe.db.set_single_value("OpenWA Settings", "webhook_secret", "test-secret")
        frappe.db.commit()

    def test_handle_text_message_dispatches_command(self):
        """Incoming 'invoice SINV-001' triggers invoice command."""
        payload = {
            "type": "message",
            "session": "default",
            "message": {
                "id": "msg_123",
                "from": "919999999999@c.us",
                "body": "invoice SINV-001",
            }
        }

        with patch("kreativ_notification.notification.inbound._handle_invoice_request") as mock_invoice:
            with patch("kreativ_notification.notification.inbound.check_session_health", return_value=True):
                _handle_text_message(payload)

        mock_invoice.assert_called_once_with("919999999999@c.us", "SINV-001")

    def test_handle_ledger_command(self):
        """Incoming 'ledger Customer Name' triggers ledger command."""
        payload = {
            "type": "message",
            "session": "default",
            "message": {
                "id": "msg_124",
                "from": "919999999999@c.us",
                "body": "ledger Kreativ",
            }
        }

        with patch("kreativ_notification.notification.inbound._send_ledger_pdf") as mock_ledger:
            with patch("kreativ_notification.notification.inbound.check_session_health", return_value=True):
                _handle_text_message(payload)

        mock_ledger.assert_called_once_with("919999999999@c.us", "Kreativ")

    def test_unknown_command_sends_help(self):
        """Unknown text sends help message."""
        payload = {
            "type": "message",
            "session": "default",
            "message": {
                "id": "msg_125",
                "from": "919999999999@c.us",
                "body": "random text",
            }
        }

        with patch("kreativ_notification.notification.inbound.send_text") as mock_send:
            with patch("kreativ_notification.notification.inbound.check_session_health", return_value=True):
                _handle_text_message(payload)

        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], "919999999999@c.us")
        self.assertIn("invoice", args[1].lower())
        self.assertIn("ledger", args[1].lower())

    def test_session_closed_responds_503(self):
        """Closed OpenWA session returns 503 (not 200)."""
        frappe.db.set_single_value("OpenWA Settings", "webhook_secret", "test-secret")
        frappe.db.commit()

        req = Mock()
        req.data = json.dumps({"type": "session.status", "session": "default", "status": "closed"}).encode()
        req.headers = {"X-OpenWA-Signature": "invalid"}  # Won't be checked for session.status

        # Session status doesn't check signature
        with patch("kreativ_notification.notification.inbound.check_session_health", return_value=False):
            response = receive(req)

        # Should acknowledge with 200 (we received it) but log alert
        self.assertEqual(response[0], "OK")
        self.assertEqual(response[1], 200)


class TestWebhookEndpointHTTPStatuses(IntegrationTestCase):
    """Test receive() returns proper HTTP status codes per security.py _respond()."""

    def setUp(self):
        super().setUp()
        frappe.db.set_single_value("OpenWA Settings", "webhook_secret", "correct-secret")
        frappe.db.commit()

    def test_signature_failure_returns_401(self):
        """Invalid signature -> 401 Unauthorized."""
        payload = {"type": "message", "session": "default", "message": {"id": "1", "from": "x", "body": "x"}}
        req = Mock()
        req.data = json.dumps(payload).encode()
        req.headers = {"X-OpenWA-Signature": hmac.new(b"wrong", req.data, hashlib.sha256).hexdigest()}

        response = receive(req)
        self.assertEqual(response[1], 401)
        self.assertIn("Unauthorized", response[0])

    def test_empty_payload_returns_400(self):
        """Empty body -> 400 Bad Request."""
        frappe.db.set_single_value("OpenWA Settings", "webhook_secret", "secret")
        frappe.db.commit()

        req = Mock()
        req.data = b""
        req.headers = {"X-OpenWA-Signature": "sig"}

        response = receive(req)
        self.assertEqual(response[1], 400)
        self.assertIn("Bad Request", response[0])

    def test_valid_message_returns_202(self):
        """Valid message accepted -> 202 Accepted (processed async)."""
        payload = {
            "type": "message",
            "session": "default",
            "message": {"id": "1", "from": "919999999999@c.us", "body": "ping"}
        }
        req = Mock()
        req.data = json.dumps(payload).encode()
        req.headers = {"X-OpenWA-Signature": hmac.new(b"secret", req.data, hashlib.sha256).hexdigest()}

        with patch("kreativ_notification.notification.inbound._handle_text_message"):
            with patch("kreativ_notification.notification.inbound.check_session_health", return_value=True):
                response = receive(req)

        self.assertEqual(response[1], 202)
        self.assertIn("Accepted", response[0])

    def test_processing_error_returns_500(self):
        """Unhandled exception in handler -> 500 Internal Server Error."""
        payload = {"type": "message", "session": "default", "message": {"id": "1", "from": "x", "body": "x"}}
        req = Mock()
        req.data = json.dumps(payload).encode()
        req.headers = {"X-OpenWA-Signature": hmac.new(b"secret", req.data, hashlib.sha256).hexdigest()}

        with patch("kreativ_notification.notification.inbound._handle_text_message", side_effect=ValueError("boom")):
            with patch("kreativ_notification.notification.inbound.check_session_health", return_value=True):
                response = receive(req)

        self.assertEqual(response[1], 500)
        self.assertIn("Internal", response[0])


class TestSessionStatusHandling(IntegrationTestCase):
    """Test session status webhook events."""

    def setUp(self):
        super().setUp()
        frappe.db.set_single_value("OpenWA Settings", "webhook_secret", "secret")
        frappe.db.commit()

    def test_session_qr_code_logged(self):
        """QR code status logs warning for manual scan."""
        req = Mock()
        req.data = json.dumps({"type": "session.status", "session": "default", "status": "qr", "qrCode": "data"}).encode()
        req.headers = {}

        response = receive(req)
        self.assertEqual(response[1], 200)

    def test_session_ready_clears_breaker(self):
        """Session ready clears circuit breaker."""
        req = Mock()
        req.data = json.dumps({"type": "session.status", "session": "default", "status": "ready"}).encode()
        req.headers = {}

        # Set a fake failure streak
        frappe.cache().set_value("notif_breaker:fails:default", 5)
        frappe.cache().set_value("notif_breaker:open_until:default", 9999999999)

        with patch("kreativ_notification.notification.inbound.reset_circuit_breaker") as mock_reset:
            response = receive(req)

        mock_reset.assert_called_once()
        self.assertEqual(response[1], 200)


class TestSecurityUtilities(IntegrationTestCase):
    """Test security.py helpers."""

    def test_respond_returns_tuple(self):
        """_respond returns (body, status_code) tuple."""
        body, code = _respond("OK", 200)
        self.assertEqual(body, "OK")
        self.assertEqual(code, 200)

    def test_webhook_autherror_has_status(self):
        """WebhookAuthError carries HTTP status."""
        err = WebhookAuthError("bad sig", 401)
        self.assertEqual(err.http_status, 401)
        self.assertEqual(str(err), "bad sig")


if __name__ == "__main__":
    unittest.main()