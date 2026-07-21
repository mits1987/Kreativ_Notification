"""Tests for inbound.py - webhook endpoint and bot logic."""
import json
import unittest
from unittest.mock import Mock, patch, MagicMock
import hmac
import hashlib
from frappe.tests import IntegrationTestCase
import frappe


class TestInboundWebhook(IntegrationTestCase):
    """Tests for inbound webhook signature verification."""

    def test_valid_signature(self):
        """Correct HMAC-SHA256 signature passes."""
        from kreativ_notification.notification.security import verify_webhook_signature

        secret = "test-secret-123"
        payload = b'{"message": "hello"}'
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        self.assertTrue(verify_webhook_signature(payload, sig, secret))

    def test_invalid_signature_rejected(self):
        """Wrong signature fails."""
        from kreativ_notification.notification.security import verify_webhook_signature

        secret = "test-secret-123"
        payload = b'{"message": "hello"}'
        bad_sig = hmac.new(b"wrong-secret", payload, hashlib.sha256).hexdigest()

        self.assertFalse(verify_webhook_signature(payload, bad_sig, secret))

    def test_missing_header_rejected(self):
        """Missing signature fails."""
        from kreativ_notification.notification.security import verify_webhook_signature

        self.assertFalse(verify_webhook_signature(b'{}', "", "secret"))

    def test_empty_body_rejected(self):
        """Empty body fails."""
        from kreativ_notification.notification.security import verify_webhook_signature

        self.assertFalse(verify_webhook_signature(b"", "sig", "secret"))


class TestBotHelpers(IntegrationTestCase):
    """Test bot helper logic."""

    @patch("kreativ_notification.notification.inbound._send_text")
    def test_handle_invoice_not_found(self, mock_send_text):
        """Invoice not found sends error message."""
        from kreativ_notification.notification.inbound import _handle_invoice_request

        with patch("kreativ_notification.notification.inbound._find_sales_invoice", return_value=None):
            _handle_invoice_request("919999999999@c.us", "NONEXISTENT")
        mock_send_text.assert_called_once()
        args = mock_send_text.call_args[0]
        self.assertIn("not found", args[1].lower())

    @patch("kreativ_notification.notification.inbound._search_customers")
    @patch("kreativ_notification.notification.inbound._send_text")
    def test_handle_ledger_no_customers(self, mock_send_text, mock_search):
        """No matching customers sends error."""
        from kreativ_notification.notification.inbound import _handle_ledger_request

        mock_search.return_value = []

        _handle_ledger_request("919999999999@c.us", "NonExistent")
        mock_send_text.assert_called_once()
        args = mock_send_text.call_args[0]
        self.assertIn("no customers found", args[1].lower())


class TestParseInvoiceReference(IntegrationTestCase):
    """Test invoice keyword parsing."""

    @patch("kreativ_notification.notification.inbound.frappe.get_cached_doc")
    def test_parse_invoice_keyword(self, mock_get_cached_doc):
        """Extracts invoice number after keyword."""
        from kreativ_notification.notification.inbound import _parse_invoice_reference

        mock_settings = Mock()
        mock_settings.invoice_keywords = "invoice,inv"
        mock_get_cached_doc.return_value = mock_settings

        result = _parse_invoice_reference("invoice SINV-001", mock_settings)
        self.assertEqual(result, "SINV-001")

    @patch("kreativ_notification.notification.inbound.frappe.get_cached_doc")
    def test_parse_ledger_keyword(self, mock_get_cached_doc):
        """Extracts customer name after ledger keyword."""
        from kreativ_notification.notification.inbound import _parse_ledger_reference

        mock_settings = Mock()
        mock_settings.ledger_keywords = "ledger,statement"
        mock_get_cached_doc.return_value = mock_settings

        result = _parse_ledger_reference("ledger Kreativ", mock_settings)
        self.assertEqual(result, "Kreativ")


class TestHelpText(IntegrationTestCase):
    """Test help text generation."""

    def test_help_contains_keywords(self):
        """Help text mentions invoice and ledger commands."""
        from kreativ_notification.notification.inbound import _get_help_text

        help_text = _get_help_text()
        self.assertIn("invoice", help_text.lower())
        self.assertIn("ledger", help_text.lower())
        self.assertIn("help", help_text.lower())


class TestRateLimitCheck(IntegrationTestCase):
    """Test inbound rate limiting."""

    @patch("kreativ_notification.notification.inbound.frappe.cache")
    def test_rate_limit_allows_under_limit(self, mock_cache):
        """Allows messages under limit."""
        from kreativ_notification.notification.inbound import _check_rate_limit

        mock_cache_instance = MagicMock()
        mock_cache_instance.get_value.return_value = 5
        mock_cache.return_value = mock_cache_instance

        self.assertTrue(_check_rate_limit("919999999999@c.us"))

    @patch("kreativ_notification.notification.inbound.frappe.cache")
    def test_rate_limit_blocks_over_limit(self, mock_cache):
        """Blocks messages over limit."""
        from kreativ_notification.notification.inbound import _check_rate_limit

        mock_cache_instance = MagicMock()
        mock_cache_instance.get_value.return_value = 15
        mock_cache.return_value = mock_cache_instance

        self.assertFalse(_check_rate_limit("919999999999@c.us"))


class TestExtractMessageText(IntegrationTestCase):
    """Test message text extraction from various message types."""

    def test_extract_conversation(self):
        """Extracts text from conversation message."""
        from kreativ_notification.notification.inbound import _extract_message_text

        msg = {"conversation": "Hello world"}
        self.assertEqual(_extract_message_text(msg), "Hello world")

    def test_extract_extended_text(self):
        """Extracts text from extendedTextMessage."""
        from kreativ_notification.notification.inbound import _extract_message_text

        msg = {"extendedTextMessage": {"text": "Extended text"}}
        self.assertEqual(_extract_message_text(msg), "Extended text")

    def test_extract_image_caption(self):
        """Extracts caption from image message."""
        from kreativ_notification.notification.inbound import _extract_message_text

        msg = {"imageMessage": {"caption": "Image caption"}}
        self.assertEqual(_extract_message_text(msg), "Image caption")

    def test_extract_document_caption(self):
        """Extracts caption from document message."""
        from kreativ_notification.notification.inbound import _extract_message_text

        msg = {"documentMessage": {"caption": "Doc caption"}}
        self.assertEqual(_extract_message_text(msg), "Doc caption")


class TestConversationState(IntegrationTestCase):
    """Test conversation state helpers."""

    @patch("kreativ_notification.notification.inbound.frappe.cache")
    def test_save_get_clear_conversation(self, mock_cache):
        """Save, get, and clear conversation state."""
        from kreativ_notification.notification.inbound import (
            _save_conversation_state, _get_conversation_state, _clear_conversation_state
        )

        mock_cache_instance = MagicMock()
        mock_cache.return_value = mock_cache_instance

        state = {"type": "ledger_selection", "customers": [{"name": "CUST-001"}], "created_at": 123456}
        _save_conversation_state("919999999999@c.us", state)

        mock_cache_instance.set_value.assert_called_once()

        mock_cache_instance.get_value.return_value = state
        retrieved = _get_conversation_state("919999999999@c.us")
        self.assertEqual(retrieved, state)

        _clear_conversation_state("919999999999@c.us")
        mock_cache_instance.delete_value.assert_called_once()


if __name__ == "__main__":
    unittest.main()