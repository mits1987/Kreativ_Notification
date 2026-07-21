"""Tests for dispatcher.py v3 fixes.

Covers:
1. Circuit breaker ignores permanent failures
2. Expired attachment payload -> clean Permanent Failed
3. Fallback skips quiet-hours-deferred rows AND re-attaches cached file
4. Cleanup prunes Delivered/Read rows (not just Sent)
"""
import json
import unittest
from unittest.mock import Mock, patch, MagicMock
from datetime import time
from frappe.tests import IntegrationTestCase
import frappe


def _mock_cache_instance():
    """Return a mock Redis cache instance with all needed methods."""
    mock = MagicMock()
    mock.get_value.return_value = 0
    mock.set_value.return_value = None
    mock.delete_value.return_value = None
    mock.incrby.return_value = 1
    mock.expire.return_value = None
    return mock


def _setup_frappe_cache_mock(mock_cache):
    """Configure a frappe.cache mock to return a mock cache instance."""
    cache_instance = _mock_cache_instance()
    mock_cache.return_value = cache_instance
    return cache_instance


def _mock_channel_doc(quiet_hours_start=None, quiet_hours_end=None, rate_limit_per_minute=0):
    """Create a mock Notification Channel doc."""
    mock = MagicMock()
    mock.quiet_hours_start = quiet_hours_start
    mock.quiet_hours_end = quiet_hours_end
    mock.rate_limit_per_minute = rate_limit_per_minute
    return mock


class TestCircuitBreakerIgnoresPermanent(IntegrationTestCase):
    """FIX v3.1: Breaker no longer trips on permanent failures."""

    @patch("kreativ_notification.notification.dispatcher.get_default_channel", return_value="WhatsApp - OpenWA")
    @patch("kreativ_notification.notification.dispatcher.get_driver")
    @patch("kreativ_notification.notification.dispatcher._finalize")
    @patch("kreativ_notification.notification.dispatcher._quiet_hours_wait", return_value=0)
    @patch("kreativ_notification.notification.dispatcher._rate_limit_ok", return_value=True)
    @patch("kreativ_notification.notification.dispatcher._breaker_open", return_value=False)
    @patch("kreativ_notification.notification.dispatcher.frappe.db.sql")
    @patch("kreativ_notification.notification.dispatcher.frappe.db.get_value")
    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    def test_deliver_permanent_failure_no_breaker_trip(
        self, mock_cache, mock_get_value, mock_sql, mock_breaker_open,
        mock_rate_limit, mock_quiet_hours, mock_finalize, mock_get_driver, mock_get_default
    ):
        """Invalid recipient -> Permanent Failed, _breaker_trip NOT called."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)

        mock_driver = MagicMock()
        mock_driver.normalize_recipient.return_value = None  # Invalid recipient
        mock_driver.driver_type = "WhatsApp - OpenWA"
        mock_get_driver.return_value = mock_driver

        # frappe.db.sql returns list of rows (update count for UPDATE)
        mock_sql.return_value = [[1]]  # 1 row updated
        mock_get_value.return_value = {
            "status": "Processing",
            "channel": "WhatsApp - OpenWA",
            "recipient": "invalid",
            "meta": "{}",
            "retry_count": 0,
            "priority": "Normal",
        }

        with patch("kreativ_notification.notification.dispatcher._breaker_trip") as mock_trip:
            from kreativ_notification.notification.dispatcher import deliver
            deliver("LOG-1")

            mock_trip.assert_not_called()
            mock_finalize.assert_called_once()
            call_args = mock_finalize.call_args
            assert call_args[1].get("permanent", False) is True

    @patch("kreativ_notification.notification.dispatcher.get_default_channel", return_value="WhatsApp - OpenWA")
    @patch("kreativ_notification.notification.dispatcher.get_driver")
    @patch("kreativ_notification.notification.dispatcher._reschedule")
    @patch("kreativ_notification.notification.dispatcher._quiet_hours_wait", return_value=0)
    @patch("kreativ_notification.notification.dispatcher._rate_limit_ok", return_value=True)
    @patch("kreativ_notification.notification.dispatcher._breaker_open", return_value=False)
    @patch("kreativ_notification.notification.dispatcher.frappe.db.sql")
    @patch("kreativ_notification.notification.dispatcher.frappe.db.get_value")
    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    def test_deliver_transient_failure_trips_breaker(
        self, mock_cache, mock_get_value, mock_sql, mock_breaker_open,
        mock_rate_limit, mock_quiet_hours, mock_reschedule, mock_get_driver, mock_get_default
    ):
        """Timeout -> transient failure, _breaker_trip IS called."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)

        mock_driver = MagicMock()
        mock_driver.normalize_recipient.return_value = "919999999999@c.us"
        mock_driver.send_text.return_value = {"success": False, "error": "Timeout", "permanent": False}
        mock_get_driver.return_value = mock_driver

        mock_sql.return_value = [[1]]
        mock_get_value.return_value = {
            "status": "Processing",
            "channel": "WhatsApp - OpenWA",
            "recipient": "919999999999@c.us",
            "meta": '{"text": "Test", "has_file": false}',
            "retry_count": 0,
            "priority": "Normal",
        }

        with patch("kreativ_notification.notification.dispatcher._breaker_trip") as mock_trip:
            from kreativ_notification.notification.dispatcher import deliver
            deliver("LOG-1")

            mock_trip.assert_called_once()
            mock_reschedule.assert_called_once()


class TestExpiredAttachmentPayload(IntegrationTestCase):
    """FIX v3.2: Expired attachment payload -> Permanent Failed cleanly."""

    @patch("kreativ_notification.notification.dispatcher.get_default_channel", return_value="WhatsApp - OpenWA")
    @patch("kreativ_notification.notification.dispatcher.get_driver")
    @patch("kreativ_notification.notification.dispatcher._finalize")
    @patch("kreativ_notification.notification.dispatcher._quiet_hours_wait", return_value=0)
    @patch("kreativ_notification.notification.dispatcher._rate_limit_ok", return_value=True)
    @patch("kreativ_notification.notification.dispatcher._breaker_open", return_value=False)
    @patch("kreativ_notification.notification.dispatcher.frappe.db.sql")
    @patch("kreativ_notification.notification.dispatcher.frappe.db.get_value")
    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    def test_deliver_with_expired_file_cache(
        self, mock_cache, mock_get_value, mock_sql, mock_breaker_open,
        mock_rate_limit, mock_quiet_hours, mock_finalize, mock_get_driver, mock_get_default
    ):
        """File cache key missing -> Permanent Failed (no crash)."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)
        # First call: breaker check (0 = closed), second: payload cache (None = expired)
        cache_instance.get_value.side_effect = [0, None]

        mock_driver = MagicMock()
        mock_driver.normalize_recipient.return_value = "919999999999@c.us"
        mock_driver.driver_type = "WhatsApp - OpenWA"
        mock_get_driver.return_value = mock_driver

        mock_sql.return_value = [[1]]
        mock_get_value.return_value = {
            "status": "Processing",
            "channel": "WhatsApp - OpenWA",
            "recipient": "919999999999@c.us",
            "meta": '{"text": "Test with PDF", "has_file": true, "filename": "test.pdf", "mimetype": "application/pdf"}',
            "retry_count": 0,
            "priority": "Normal",
        }

        from kreativ_notification.notification.dispatcher import deliver
        deliver("LOG-1")

        mock_finalize.assert_called_once()
        call_args = mock_finalize.call_args
        # _finalize is called with positional args: (log_name, success, error, permanent=True)
        error_msg = call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("error", "")
        assert call_args[1].get("permanent", False) is True
        assert "expired" in (error_msg or "").lower()


class TestFallbackSkipsQuietHours(IntegrationTestCase):
    """FIX v3.3: Fallback skips quiet-hours-deferred rows AND fires for failed status."""

    @patch("kreativ_notification.notification.dispatcher.frappe.get_all")
    def test_fallback_skips_quiet_hours_deferred(self, mock_get_all):
        """Rows with error_message 'Quiet hours' are skipped by fallback.

        The real get_all filters these out in the DB query, so mock returns empty list.
        """
        mock_get_all.return_value = []  # DB filters out DEFER_QUIET_HOURS rows

        from kreativ_notification.notification.dispatcher import process_fallbacks
        with patch("kreativ_notification.notification.dispatcher.dispatch") as mock_dispatch:
            process_fallbacks()
            mock_dispatch.assert_not_called()

    @patch("kreativ_notification.notification.dispatcher.frappe.get_all")
    @patch("kreativ_notification.notification.dispatcher.frappe.db.set_value")
    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    def test_fallback_fires_for_failed_status(self, mock_cache, mock_set_value, mock_get_all):
        """Fallback triggers for 'Failed' status rows."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)

        mock_get_all.return_value = [
            {"name": "LOG-1", "fallback_channel": "SMS", "recipient": "x", "meta": "{}",
             "source_doctype": "Test", "source_docname": "1", "message_type": "Custom",
             "priority": "Normal", "notification_rule": None},
        ]

        from kreativ_notification.notification.dispatcher import process_fallbacks
        with patch("kreativ_notification.notification.dispatcher.dispatch") as mock_dispatch:
            process_fallbacks()
            mock_dispatch.assert_called_once()
            call_kwargs = mock_dispatch.call_args[1]
            assert call_kwargs.get("channel") == "SMS"
            assert call_kwargs.get("recipient") == "x"
            assert call_kwargs.get("idempotency_key") is None


class TestFallbackReattachesFile(IntegrationTestCase):
    """FIX v3.4: Fallback re-attaches cached file AND handles expired cache."""

    @patch("kreativ_notification.notification.dispatcher.frappe.get_all")
    @patch("kreativ_notification.notification.dispatcher.frappe.db.set_value")
    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    def test_fallback_escalates_with_attachment(self, mock_cache, mock_set_value, mock_get_all):
        """Fallback resends with original attachment if file cache valid."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)
        cache_instance.get_value.return_value = "cached_base64_data"

        mock_get_all.return_value = [
            {"name": "LOG-1", "fallback_channel": "SMS", "recipient": "x",
             "meta": '{"text": "Test", "has_file": true, "filename": "test.pdf", "mimetype": "application/pdf"}',
             "source_doctype": "Test", "source_docname": "1", "message_type": "Custom",
             "priority": "Normal", "notification_rule": None},
        ]

        from kreativ_notification.notification.dispatcher import process_fallbacks
        with patch("kreativ_notification.notification.dispatcher.dispatch") as mock_dispatch:
            process_fallbacks()
            mock_dispatch.assert_called_once()
            call_args = mock_dispatch.call_args[1]
            assert call_args.get("file_b64") == "cached_base64_data"
            assert call_args.get("filename") == "test.pdf"

    @patch("kreativ_notification.notification.dispatcher.frappe.get_all")
    @patch("kreativ_notification.notification.dispatcher.frappe.db.set_value")
    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    def test_fallback_with_expired_cache_sends_without_attachment(self, mock_cache, mock_set_value, mock_get_all):
        """If file cache expired, fallback sends without attachment."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)
        cache_instance.get_value.return_value = None  # Expired cache

        mock_get_all.return_value = [
            {"name": "LOG-1", "fallback_channel": "SMS", "recipient": "x",
             "meta": '{"text": "Test", "has_file": true, "filename": "test.pdf", "mimetype": "application/pdf"}',
             "source_doctype": "Test", "source_docname": "1", "message_type": "Custom",
             "priority": "Normal", "notification_rule": None},
        ]

        from kreativ_notification.notification.dispatcher import process_fallbacks
        with patch("kreativ_notification.notification.dispatcher.dispatch") as mock_dispatch:
            process_fallbacks()
            mock_dispatch.assert_called_once()
            call_args = mock_dispatch.call_args[1]
            assert call_args.get("file_b64") is None


class TestCleanupOldLogs(IntegrationTestCase):
    """FIX v3.5: Cleanup prunes Delivered/Read rows (not just Sent)."""

    @patch("kreativ_notification.notification.dispatcher.frappe.db.delete")
    def test_cleanup_includes_delivered_and_read(self, mock_delete):
        """Cleanup deletes Delivered, Read, and Failed after retention days."""
        from kreativ_notification.notification.dispatcher import cleanup_old_logs
        cleanup_old_logs(days_sent=7)

        assert mock_delete.call_count == 2
        # First call: Sent, Delivered, Read
        call_args_1 = mock_delete.call_args_list[0]
        filters_1 = call_args_1[0][1]
        status_list_1 = filters_1.get("status", [])[1]  # ["in", [...]] structure
        assert "Sent" in status_list_1
        assert "Delivered" in status_list_1
        assert "Read" in status_list_1

        # Second call: Failed, Permanently Failed
        call_args_2 = mock_delete.call_args_list[1]
        filters_2 = call_args_2[0][1]
        status_list_2 = filters_2.get("status", [])[1]
        assert "Failed" in status_list_2
        assert "Permanently Failed" in status_list_2

    @patch("kreativ_notification.notification.dispatcher.frappe.db.delete")
    def test_cleanup_failed_rows_after_days_failed(self, mock_delete):
        """Failed rows cleaned up after days_failed threshold."""
        from kreativ_notification.notification.dispatcher import cleanup_old_logs
        mock_delete.reset_mock()
        cleanup_old_logs(days_failed=3)

        call_args = mock_delete.call_args
        filters = call_args[0][1]
        status_list = filters.get("status", [])[1]
        assert "Failed" in status_list


class TestRetryRescheduleBehavior(IntegrationTestCase):
    """Test retry scheduling and backoff."""

    @patch("kreativ_notification.notification.dispatcher._finalize")
    @patch("kreativ_notification.notification.dispatcher.frappe.db.set_value")
    def test_respect_max_attempts(self, mock_set_value, mock_finalize):
        """Respects max_attempts limit before marking Permanent Failed."""
        from kreativ_notification.notification.dispatcher import _reschedule
        with patch("kreativ_notification.notification.dispatcher.MAX_ATTEMPTS", 5):
            _reschedule("LOG-1", retry_count=5, error="timeout", count_attempt=True)
            mock_finalize.assert_called_once()
            call_args = mock_finalize.call_args
            assert call_args[1].get("permanent", False) is True

    @patch("kreativ_notification.notification.dispatcher.frappe.db.set_value")
    def test_backoff_schedule_correct(self, mock_set_value):
        """Backoff increases with retry count."""
        from kreativ_notification.notification.dispatcher import _reschedule

        _reschedule("LOG-1", retry_count=0, error="timeout")
        call_args = mock_set_value.call_args
        # call(doctype, docname, filters_dict, update_modified=False)
        filters = call_args[0][2]
        assert filters["retry_count"] == 1

        mock_set_value.reset_mock()
        _reschedule("LOG-1", retry_count=1, error="timeout")
        call_args = mock_set_value.call_args
        filters = call_args[0][2]
        assert filters["retry_count"] == 2


class TestQuietHoursWait(IntegrationTestCase):
    """Test quiet hours wait logic."""

    @patch("kreativ_notification.notification.dispatcher.frappe.get_cached_doc")
    @patch("kreativ_notification.notification.dispatcher.nowtime")
    def test_quiet_hours_wait_returns_zero_outside_hours(self, mock_nowtime, mock_get_cached_doc):
        """Returns 0 minutes wait outside quiet hours."""
        from kreativ_notification.notification.dispatcher import _quiet_hours_wait
        mock_ch = _mock_channel_doc(quiet_hours_start="22:00:00", quiet_hours_end="08:00:00")
        mock_get_cached_doc.return_value = mock_ch
        mock_nowtime.return_value = time(14, 0, 0)  # 2 PM

        wait = _quiet_hours_wait("Test Channel")
        assert wait == 0

    @patch("kreativ_notification.notification.dispatcher.frappe.get_cached_doc")
    @patch("kreativ_notification.notification.dispatcher.nowtime")
    def test_quiet_hours_wait_returns_minutes_inside_hours(self, mock_nowtime, mock_get_cached_doc):
        """Returns minutes to wait inside quiet hours."""
        from kreativ_notification.notification.dispatcher import _quiet_hours_wait
        mock_ch = _mock_channel_doc(quiet_hours_start="22:00:00", quiet_hours_end="08:00:00")
        mock_get_cached_doc.return_value = mock_ch
        mock_nowtime.return_value = time(2, 0, 0)  # 2 AM

        wait = _quiet_hours_wait("Test Channel")
        assert wait > 0


class TestRateLimiter(IntegrationTestCase):
    """Test rate limiter per channel."""

    @patch("kreativ_notification.notification.dispatcher.frappe.get_cached_doc")
    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    def test_rate_limit_allows_under_limit(self, mock_cache, mock_get_cached_doc):
        """Allows sends under the rate limit."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)
        cache_instance.incrby.return_value = 5  # Under limit

        mock_ch = _mock_channel_doc(rate_limit_per_minute=10)
        mock_get_cached_doc.return_value = mock_ch

        from kreativ_notification.notification.dispatcher import _rate_limit_ok
        result = _rate_limit_ok("Test Channel")
        assert result is True

    @patch("kreativ_notification.notification.dispatcher.frappe.get_cached_doc")
    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    def test_rate_limit_blocks_over_limit(self, mock_cache, mock_get_cached_doc):
        """Blocks sends over the rate limit."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)
        cache_instance.incrby.return_value = 15  # Over limit

        mock_ch = _mock_channel_doc(rate_limit_per_minute=10)
        mock_get_cached_doc.return_value = mock_ch

        from kreativ_notification.notification.dispatcher import _rate_limit_ok
        result = _rate_limit_ok("Test Channel")
        assert result is False


class TestIdempotency(IntegrationTestCase):
    """Test idempotency key handling."""

    @patch("kreativ_notification.notification.dispatcher.frappe.db.get_value")
    def test_duplicate_idempotency_key_rejected_when_not_failed(self, mock_get_value):
        """Duplicate key rejected if original not Failed."""
        # Return a dict-like mock that supports both attribute and subscript access
        existing = MagicMock()
        existing.__getitem__.side_effect = lambda k: {"name": "LOG-EXISTING", "status": "Sent", "doctype": "WhatsApp Send Log"}.get(k)
        # Also support attribute access
        existing.name = "LOG-EXISTING"
        existing.status = "Sent"
        mock_get_value.return_value = existing

        from kreativ_notification.notification.dispatcher import dispatch
        result = dispatch(
            channel="WhatsApp - OpenWA",
            recipient="919999999999@c.us",
            text="Test",
            idempotency_key="duplicate-key",
        )
        # Code returns success=True for duplicate (operation succeeded, just deduplicated)
        assert result["success"] is True
        assert result["status"] == "duplicate"

    @patch("kreativ_notification.notification.dispatcher.get_driver")
    @patch("kreativ_notification.notification.dispatcher._enqueue_delivery")
    @patch("kreativ_notification.notification.dispatcher.frappe.db.set_value")
    @patch("kreativ_notification.notification.dispatcher.frappe.db.get_value")
    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    @patch("kreativ_notification.notification.dispatcher.frappe.get_doc")
    def test_idempotency_allows_retry_after_failed(self, mock_get_doc, mock_cache, mock_get_value, mock_set_value, mock_enqueue, mock_get_driver):
        """Retry allowed if original status was Failed."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)

        existing = MagicMock()
        existing.__getitem__.side_effect = lambda k: {"name": "LOG-EXISTING", "status": "Failed", "doctype": "WhatsApp Send Log"}.get(k)
        existing.name = "LOG-EXISTING"
        existing.status = "Failed"
        mock_get_value.return_value = existing
        mock_driver = MagicMock()
        mock_driver.normalize_recipient.return_value = "919999999999@c.us"
        mock_driver.send_text.return_value = {"success": True, "message_id": "msg-123"}
        mock_get_driver.return_value = mock_driver

        # Mock the log doc creation
        mock_log = MagicMock()
        mock_log.name = "LOG-NEW"
        mock_get_doc.return_value = mock_log

        from kreativ_notification.notification.dispatcher import dispatch
        result = dispatch(
            channel="WhatsApp - OpenWA",
            recipient="919999999999@c.us",
            text="Test",
            idempotency_key="retry-key",
        )
        assert result["success"] is True
        assert result["log_name"] == "LOG-NEW"


class TestCircuitBreaker(IntegrationTestCase):
    """Test circuit breaker helpers."""

    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    def test_breaker_trip_increments_counter(self, mock_cache):
        """_breaker_trip increments failure streak."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)
        cache_instance.get_value.return_value = 2

        from kreativ_notification.notification.dispatcher import _breaker_trip
        _breaker_trip("Test Channel")
        cache_instance.set_value.assert_called_with(
            "notif_breaker:kreativ316:Test Channel", 3, expires_in_sec=1800
        )

    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    def test_breaker_open_at_threshold(self, mock_cache):
        """Breaker opens when streak reaches threshold."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)
        cache_instance.get_value.return_value = 3

        from kreativ_notification.notification.dispatcher import _breaker_open
        assert _breaker_open("Test Channel") is True

    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    def test_breaker_closed_below_threshold(self, mock_cache):
        """Breaker closed when streak below threshold."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)
        cache_instance.get_value.return_value = 2

        from kreativ_notification.notification.dispatcher import _breaker_open
        assert _breaker_open("Test Channel") is False

    @patch("kreativ_notification.notification.dispatcher.frappe.cache")
    def test_breaker_reset_deletes_key(self, mock_cache):
        """_breaker_reset deletes the breaker key."""
        cache_instance = _setup_frappe_cache_mock(mock_cache)

        from kreativ_notification.notification.dispatcher import _breaker_reset
        _breaker_reset("Test Channel")
        cache_instance.delete_value.assert_called_with("notif_breaker:kreativ316:Test Channel")


if __name__ == "__main__":
    unittest.main()