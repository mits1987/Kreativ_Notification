# Copyright (c) 2026, Kreativ Gravures
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class NotificationChannel(Document):
    def validate(self):
        self._ensure_single_default()
        self._validate_quiet_hours()

    def _ensure_single_default(self):
        if not self.is_default:
            return
        others = frappe.get_all(
            "Notification Channel",
            filters={"is_default": 1, "name": ["!=", self.name]},
            pluck="name",
        )
        for name in others:
            frappe.db.set_value("Notification Channel", name, "is_default", 0)

    def _validate_quiet_hours(self):
        if bool(self.quiet_hours_start) != bool(self.quiet_hours_end):
            frappe.throw(_("Set both Quiet Hours Start and End, or neither."))


@frappe.whitelist()
def run_health_check(channel: str) -> dict:
    """Health-check one channel and persist the result (used by form button + cron)."""
    frappe.only_for(("System Manager", "WhatsApp Manager"))
    return _check_and_store(channel)


def _check_and_store(channel: str) -> dict:
    from kreativ_notification.notification.channels import get_driver

    try:
        driver = get_driver(channel)
        health = driver.get_health()
    except Exception as e:
        health = {"healthy": False, "status": "error", "detail": str(e)}

    frappe.db.set_value("Notification Channel", channel, {
        "health_status": health.get("status", "unknown"),
        "health_detail": (health.get("detail") or "")[:400],
        "last_health_check": now_datetime(),
    }, update_modified=False)
    frappe.db.commit()
    return health


def check_all_channels():
    """Cron: health-check every enabled channel; alert admin via a healthy
    fallback channel if one goes down."""
    channels = frappe.get_all("Notification Channel", filters={"enabled": 1}, pluck="name")
    unhealthy = []
    for name in channels:
        health = _check_and_store(name)
        if not health.get("healthy"):
            unhealthy.append((name, health))

    if unhealthy:
        _alert_admin(unhealthy)


def _alert_admin(unhealthy: list):
    """Email the admin about down channels — deliberately NOT via the
    broken channel itself."""
    lines = [f"- {name}: {h.get('status')} {h.get('detail', '')}" for name, h in unhealthy]
    subject = "Notification channel unhealthy: " + ", ".join(n for n, _h in unhealthy)

    # Throttle: one alert per hour per set of down channels
    cache_key = "notif_channel_alert:" + ",".join(sorted(n for n, _h in unhealthy))
    if frappe.cache().get_value(cache_key):
        return
    frappe.cache().set_value(cache_key, 1, expires_in_sec=3600)

    try:
        from frappe.utils.user import get_system_managers
        recipients = get_system_managers(only_name=False)
        if not recipients:
            return
        frappe.sendmail(
            recipients=recipients,
            subject=subject,
            message="<br>".join(lines),
            delayed=False,
        )
    except Exception:
        frappe.log_error(title="Channel health alert failed",
                         message=frappe.get_traceback())


@frappe.whitelist()
def send_test_message(channel: str, recipient: str, text: str = None) -> dict:
    """Send a test through the full pipeline (dispatcher → driver)."""
    frappe.only_for(("System Manager", "WhatsApp Manager"))
    from kreativ_notification.notification.dispatcher import dispatch

    return dispatch(
        channel=channel,
        recipient=recipient,
        message_type="Test",
        text=text or _("Test message from Kreativ Notification."),
        source_doctype="Notification Channel",
        source_docname=channel,
        priority="Urgent",  # bypass quiet hours
    )
