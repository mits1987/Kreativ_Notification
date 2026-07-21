"""Tests for rules_engine.py - pagination fix and idempotency."""
import json
import unittest
from unittest.mock import Mock, patch, MagicMock
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate, add_days
import frappe

# Initialize Frappe before any imports that use frappe
frappe.local.site = "kreativ316"
frappe.local.request = None
frappe.local.response = {}
frappe.local.form_dict = {}

from kreativ_notification.notification.rules_engine import evaluate_date_rules, _process_rule, clear_rule_cache, _get_shift_hours_for_out


class TestDateRulesPagination(IntegrationTestCase):
    """FIX: evaluate_date_rules uses paginated loop (no 500 cap)."""

    def test_evaluate_date_rules_paginates_over_500(self):
        """More than 500 matching docs → processes all in 500-item chunks."""
        rule = frappe.get_doc({
            "doctype": "Notification Rule",
            "rule_name": "Test Date Rule",
            "document_type": "Test DocType",
            "event": "Days Before",
            "date_field": "due_date",
            "days_offset": 0,
            "enabled": 1,
        })
        rule.insert(ignore_permissions=True)

        with patch("frappe.get_meta") as mock_get_meta:
            mock_meta = Mock()
            mock_meta.has_field.return_value = True
            mock_meta.is_submittable = False
            mock_get_meta.return_value = mock_meta

            # Track calls: first 500, second 300, third empty
            call_count = [0]
            def mock_get_all(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [f"DOC-{i}" for i in range(500)]
                elif call_count[0] == 2:
                    return [f"DOC-{i}" for i in range(500, 800)]
                return []

            with patch("frappe.get_all", side_effect=mock_get_all):
                with patch("frappe.get_doc") as mock_get_doc:
                    mock_get_doc.return_value = Mock()
                    with patch("kreativ_notification.notification.rules_engine._process_rule") as mock_process:
                        evaluate_date_rules()

            # Should call _process_rule for all 800 docs
            self.assertEqual(mock_process.call_count, 800)

    def test_evaluate_date_rules_stops_at_empty_page(self):
        """Stops when a page returns fewer than 500 docs."""
        rule = frappe.get_doc({
            "doctype": "Notification Rule",
            "rule_name": "Test Date Rule 2",
            "document_type": "Test DocType",
            "event": "Days Before",
            "date_field": "due_date",
            "days_offset": 0,
            "enabled": 1,
        })
        rule.insert(ignore_permissions=True)

        with patch("frappe.get_meta") as mock_get_meta:
            mock_meta = Mock()
            mock_meta.has_field.return_value = True
            mock_meta.is_submittable = False
            mock_get_meta.return_value = mock_meta

            # First call: 500, Second: 200 (less than 500 -> stop)
            call_count = [0]
            def mock_get_all(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [f"DOC-{i}" for i in range(500)]
                elif call_count[0] == 2:
                    return [f"DOC-{i}" for i in range(500, 700)]
                return []

            with patch("frappe.get_all", side_effect=mock_get_all):
                with patch("frappe.get_doc") as mock_get_doc:
                    mock_get_doc.return_value = Mock()
                    with patch("kreativ_notification.notification.rules_engine._process_rule") as mock_process:
                        evaluate_date_rules()

            # Exactly 2 pages
            self.assertEqual(mock_process.call_count, 700)


class TestIdempotencyKeys(IntegrationTestCase):
    """Test idempotency key generation and deduplication."""

    def test_value_change_idempotency_includes_new_value(self):
        """Value Change rule includes new value in idempotency key."""
        rule = frappe.get_doc({
            "doctype": "Notification Rule",
            "rule_name": "VC Test Rule",
            "document_type": "Sales Invoice",
            "event": "Value Change",
            "value_changed_field": "status",
            "enabled": 1,
            "message_template": "MT-001",
            "channel": "WhatsApp - OpenWA",
        })
        rule.insert(ignore_permissions=True)

        doc = Mock()
        doc.doctype = "Sales Invoice"
        doc.name = "SI-001"
        doc.get.return_value = "Paid"  # New value
        doc.has_value_changed.return_value = True

        template_mock = Mock()
        template_mock.enabled = True
        template_mock.render.return_value = {"body": "Test", "subject": ""}
        template_mock.attach_print = False
        template_mock.print_format = None
        template_mock.meta_template_name = None
        template_mock.meta_template_language = "en"

        rule_mock = Mock()
        rule_mock.name = "VC Test Rule"
        rule_mock.event = "Value Change"
        rule_mock.value_changed_field = "status"
        rule_mock.applies_to.return_value = True
        rule_mock.resolve_recipients.return_value = ["919999999999@c.us"]
        rule_mock.get_recipient_language.return_value = "en"
        rule_mock.channel = "WhatsApp - OpenWA"
        rule_mock.fallback_channel = None
        rule_mock.fallback_after_minutes = 30
        rule_mock.priority = "Normal"
        rule_mock.message_template = "MT-001"

        with patch("frappe.get_cached_doc", side_effect=lambda dt, name: rule_mock if dt == "Notification Rule" else template_mock):
            with patch("kreativ_notification.notification.rules_engine.dispatch") as mock_dispatch:
                _process_rule("VC Test Rule", doc, "Value Change")

        call_args = mock_dispatch.call_args[1]
        idem_key = call_args["idempotency_key"]
        self.assertIn("SI-001", idem_key)
        self.assertIn("Paid", idem_key)  # New value included
        self.assertIn(":Value Change:Paid:", idem_key)

    def test_value_change_skips_if_field_unchanged(self):
        """Value Change rule skips when watched field hasn't changed."""
        rule = frappe.get_doc({
            "doctype": "Notification Rule",
            "rule_name": "VC Test Rule 2",
            "document_type": "Sales Invoice",
            "event": "Value Change",
            "value_changed_field": "status",
            "enabled": 1,
            "message_template": "MT-001",
            "channel": "WhatsApp - OpenWA",
        })
        rule.insert(ignore_permissions=True)

        doc = Mock()
        doc.doctype = "Sales Invoice"
        doc.name = "SI-002"
        doc.has_value_changed.return_value = False  # Field unchanged

        with patch("frappe.get_cached_doc", return_value=Mock()):
            with patch("kreativ_notification.notification.rules_engine.dispatch") as mock_dispatch:
                _process_rule("VC Test Rule 2", doc, "Value Change")

        # No dispatch called (early return)
        mock_dispatch.assert_not_called()


class TestRuleProcessing(IntegrationTestCase):
    """Test rule processing logic."""

    def test_applies_to_filters_document(self):
        """applies_to() filters by condition."""
        rule_doc = frappe.get_doc({
            "doctype": "Notification Rule",
            "rule_name": "Filter Rule",
            "document_type": "Sales Invoice",
            "event": "New",
            "enabled": 1,
            "condition": "doc.grand_total > 1000",
            "message_template": "MT-001",
            "channel": "WhatsApp - OpenWA",
        })
        rule_doc.insert(ignore_permissions=True)

        doc = Mock()
        doc.doctype = "Sales Invoice"
        doc.name = "SI-003"
        doc.grand_total = 500  # Below threshold

        template_mock = Mock()
        template_mock.enabled = True
        template_mock.render.return_value = {"body": "Test", "subject": ""}
        template_mock.attach_print = False

        with patch("frappe.get_cached_doc", side_effect=lambda dt, name: rule_doc if dt == "Notification Rule" else template_mock):
            with patch("kreativ_notification.notification.rules_engine.dispatch") as mock_dispatch:
                _process_rule("Filter Rule", doc, "New")

        # Should not dispatch (grand_total <= 1000)
        mock_dispatch.assert_not_called()

    def test_disabled_template_skips_dispatch(self):
        """Disabled message template skips dispatch."""
        rule_doc = frappe.get_doc({
            "doctype": "Notification Rule",
            "rule_name": "Disabled Template Rule",
            "document_type": "Sales Invoice",
            "event": "New",
            "enabled": 1,
            "message_template": "MT-Disabled",
            "channel": "WhatsApp - OpenWA",
        })
        rule_doc.insert(ignore_permissions=True)

        doc = Mock()
        doc.doctype = "Sales Invoice"
        doc.name = "SI-004"

        template_mock = Mock()
        template_mock.enabled = False  # Disabled!

        with patch("frappe.get_cached_doc", side_effect=lambda dt, name: rule_doc if dt == "Notification Rule" else template_mock):
            with patch("kreativ_notification.notification.rules_engine.dispatch") as mock_dispatch:
                _process_rule("Disabled Template Rule", doc, "New")

        mock_dispatch.assert_not_called()


class TestShiftHoursHelper(IntegrationTestCase):
    """Test _get_shift_hours_for_out helper."""

    def test_calculates_from_attendance_shift(self):
        """Uses KG Employee Attendance Shift when available."""
        frappe.get_doc({
            "doctype": "DocType",
            "name": "KG Employee Attendance Shift",
            "module": "Test",
        }).insert(ignore_permissions=True)

        frappe.get_doc({
            "doctype": "KG Employee Attendance Shift",
            "employee": "EMP-001",
            "check_out": "2024-01-15 18:00:00",
            "worked_hours": "09:00",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

        result = _get_shift_hours_for_out("EMP-001", "2024-01-15 18:00:00")
        self.assertEqual(result, "09:00")

    def test_falls_back_to_checkin_if_no_shift_doctype(self):
        """Falls back to Employee Checkin when shift doctype missing."""
        # Don't create KG Employee Attendance Shift doctype
        frappe.get_doc({
            "doctype": "Employee Checkin",
            "employee": "EMP-002",
            "log_type": "IN",
            "time": "2024-01-15 09:00:00",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

        result = _get_shift_hours_for_out("EMP-002", "2024-01-15 18:00:00")
        self.assertEqual(result, "09:00")

    def test_returns_empty_on_error(self):
        """Returns empty string on any error (never breaks transaction)."""
        result = _get_shift_hours_for_out("NONEXISTENT", "invalid-date")
        self.assertEqual(result, "")


class TestClearRuleCache(IntegrationTestCase):
    """Test cache invalidation."""

    def test_clear_rule_cache_deletes_prefix(self):
        """clear_rule_cache deletes all notif_rules: keys."""
        frappe.cache().set_value("notif_rules:Sales Invoice:New", ["rule1"])
        frappe.cache().set_value("notif_rules:Purchase Order:Submit", ["rule2"])
        frappe.cache().set_value("other_key", "value")

        clear_rule_cache()

        self.assertIsNone(frappe.cache().get_value("notif_rules:Sales Invoice:New"))
        self.assertIsNone(frappe.cache().get_value("notif_rules:Purchase Order:Submit"))
        self.assertEqual(frappe.cache().get_value("other_key"), "value")


if __name__ == "__main__":
    unittest.main()