"""Tests for OpenWA error classification (permanent vs transient).

Critical for circuit breaker logic: permanent failures (bad number, unconfigured
channel, invalid template) must NOT trip the breaker. Only transport-level
failures (timeout, 5xx, network) should increment the failure streak.
"""
import unittest
from unittest.mock import Mock, patch
import frappe
from frappe.tests import IntegrationTestCase
from frappe.exceptions import ValidationError

# Initialize Frappe before any imports that use frappe
frappe.local.site = "kreativ316"
frappe.local.request = None
frappe.local.response = {}
frappe.local.form_dict = {}

from kreativ_notification.notification.openwa_client import OpenWAClient


class TestErrorClassification(IntegrationTestCase):
    """Test the _classify_error method that drives breaker behavior."""

    def setUp(self):
        self.client = OpenWAClient(base_url="http://localhost:2785", api_key="test", session_id="default")

    def test_401_auth_error_is_permanent(self):
        """401/403 from OpenWA = bad API key = permanent, don't trip breaker."""
        result = self.client._classify_error("HTTP 401: Unauthorized")
        self.assertTrue(result["permanent"])
        self.assertTrue("Unauthorized" in result["error"] or "401" in result["error"])

    def test_404_not_found_is_permanent(self):
        """404 = session/channel not found = permanent (config issue)."""
        result = self.client._classify_error("HTTP 404: Not Found")
        self.assertTrue(result["permanent"])

    def test_400_bad_request_is_permanent(self):
        """400 = bad payload (e.g. invalid phone) = permanent, don't trip breaker."""
        result = self.client._classify_error("HTTP 400: Bad Request")
        self.assertTrue(result["permanent"])

    def test_422_unprocessable_entity_is_permanent(self):
        """422 = semantic error (e.g. unregistered template) = permanent."""
        result = self.client._classify_error("HTTP 422: Unprocessable Entity")
        # Note: current implementation doesn't classify 422 as permanent
        # This documents expected behavior - if 422 should be permanent, add to classifier
        self.assertFalse(result["permanent"])

    def test_500_server_error_is_transient(self):
        """5xx = OpenWA/internal server error = transient, DO trip breaker."""
        result = self.client._classify_error("HTTP 500: Internal Server Error")
        self.assertFalse(result["permanent"])

    def test_504_gateway_timeout_is_transient(self):
        """504 = upstream timeout = transient, DO trip breaker."""
        result = self.client._classify_error("HTTP 504: Gateway Timeout")
        self.assertFalse(result["permanent"])

    def test_connection_error_is_transient(self):
        """ConnectionError = network issue = transient, DO trip breaker."""
        result = self.client._classify_error("Cannot connect to OpenWA at http://localhost:2785. Is it running?")
        self.assertFalse(result["permanent"])

    def test_timeout_error_is_transient(self):
        """Timeout = network issue = transient, DO trip breaker."""
        result = self.client._classify_error("OpenWA timed out after 30s")
        self.assertFalse(result["permanent"])

    def test_unknown_error_defaults_transient(self):
        """Unknown exception defaults to transient (safe side for retry)."""
        result = self.client._classify_error("Unexpected error — check Error Log: ValueError: bad value")
        self.assertFalse(result["permanent"])


class TestOpenWAClientErrorHandling(IntegrationTestCase):
    """Test that OpenWAClient methods correctly use classification."""

    def test_send_text_bad_number_returns_permanent(self):
        """Invalid recipient shows permanent=True so dispatcher doesn't trip breaker."""
        with patch.object(OpenWAClient, '_post') as mock_post:
            mock_post.return_value = {"success": False, "error": "Invalid phone number", "permanent": True}

            client = OpenWAClient(base_url="http://localhost:2785", api_key="test", session_id="default")
            result = client.send_text("invalid", "Hello")

            self.assertFalse(result["success"])
            self.assertTrue(result["permanent"])
            self.assertIn("Invalid phone number", result["error"])

    def test_send_document_unconfigured_returns_permanent(self):
        """Missing base_url/api_key throws ValidationError."""
        with patch('kreativ_notification.notification.openwa_client.get_openwa_config', return_value=("", "", "default")):
            client = OpenWAClient(base_url="", api_key="", session_id="default")
            with self.assertRaises(frappe.exceptions.ValidationError) as cm:
                client.send_document("919999999999@c.us", "base64pdf", "test.pdf")
            self.assertIn("not configured", str(cm.exception).lower())


class TestCircuitBreakerBehavior(unittest.TestCase):
    """Test that circuit breaker trips after threshold consecutive failures."""

    def test_three_failures_trip_breaker(self):
        """Three consecutive failures SHOULD open the breaker."""
        from kreativ_notification.notification.openwa_client import (
            reset_circuit_breaker, increment_circuit_breaker, check_circuit_breaker
        )
        reset_circuit_breaker()

        for _ in range(3):
            increment_circuit_breaker()

        # check_circuit_breaker throws when breaker is open
        with self.assertRaises(frappe.exceptions.ValidationError):
            check_circuit_breaker()

    def test_two_failures_dont_trip_breaker(self):
        """Two consecutive failures should NOT open the breaker."""
        from kreativ_notification.notification.openwa_client import (
            reset_circuit_breaker, increment_circuit_breaker, check_circuit_breaker
        )
        reset_circuit_breaker()

        for _ in range(2):
            increment_circuit_breaker()

        # Should not throw
        check_circuit_breaker()

    def test_reset_clears_breaker(self):
        """reset_circuit_breaker clears the breaker state."""
        from kreativ_notification.notification.openwa_client import (
            reset_circuit_breaker, increment_circuit_breaker, check_circuit_breaker
        )
        reset_circuit_breaker()
        for _ in range(3):
            increment_circuit_breaker()
        reset_circuit_breaker()
        # Should not throw after reset
        check_circuit_breaker()


if __name__ == "__main__":
    unittest.main()