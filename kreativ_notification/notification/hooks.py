"""Doc-event handlers owned by the notification platform.

These are wired from kreativ_notification/hooks.py — NOT from other apps.
This makes the platform self-contained: installing kreativ_notification is
all that's needed for checkin + salary-slip WhatsApp delivery, and
kreativ_attendance wires only its own recalculation / payroll-lock hooks.
Frappe merges doc_events across installed apps, so both sets fire.

Both handlers only *enqueue*. The dispatcher (dispatcher.dispatch) owns
transport, retries, rate limiting, quiet hours and the circuit breaker.
"""

import frappe
from frappe.utils.background_jobs import enqueue


def on_checkin_created(doc, method=None):
    """after_insert on Employee Checkin — notify NEW punches only.

    Wrapped in try/except because after_insert has no framework-level
    exception guard in Frappe core: an uncaught error here propagates up
    through insert() into the ZKTeco sync and can roll back the punch
    transaction. A lost notification must never lose a punch.

    If the enqueue itself is lost (Redis blip, no worker), the
    retry_missed_notifications cron re-enqueues it within 10 minutes —
    it filters on whatsapp_sent in (0, None).
    """
    try:
        if not frappe.db.get_single_value("OpenWA Settings", "enabled"):
            return
        enqueue(
            "kreativ_notification.notification.employee_notifications.notify_checkin",
            queue="short",
            timeout=60,
            checkin_name=doc.name,
            enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(
            title="WhatsApp enqueue failed on checkin creation",
            message=(
                f"notify_checkin enqueue failed for {doc.name}. "
                "retry_missed_notifications (cron) will pick it up within "
                "10 minutes — no punch or notification is lost."
            ),
        )


def on_salary_slip_whatsapp(doc, method=None):
    """on_submit of Salary Slip — render the PDF and send via dispatch().

    NOTE: no frappe.db.commit() here and none in the job path before
    enqueue_after_commit — the submit transaction stays owned by Frappe.
    """
    try:
        settings = frappe.get_cached_doc("OpenWA Settings")
        if not (settings.enabled and getattr(settings, "send_salary_slips", 0)):
            return
        enqueue(
            "kreativ_notification.notification.employee_notifications.send_salary_slip",
            queue="short",
            timeout=120,
            salary_slip=doc.name,
            enqueue_after_commit=True,
        )
    except Exception:
        # Never break payroll submission because of a notification.
        frappe.log_error(
            title="Salary slip WhatsApp enqueue failed",
            message=frappe.get_traceback(),
        )