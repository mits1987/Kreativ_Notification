"""Employee-facing WhatsApp notifications: checkin alerts + salary slips.

v2 — CONSOLIDATED. Every send now flows through dispatcher.dispatch(),
the single pipeline that owns:

    - WhatsApp Send Log (outbox row created before any network call)
    - retry with exponential backoff / terminal Permanently Failed
    - per-channel rate limiting + quiet hours
    - ONE circuit breaker shared by every producer in every app

This module is therefore only responsible for:

    1. building the message text / rendering the Salary Slip PDF
    2. resolving the employee's WhatsApp number
    3. handing off to dispatch() with a stable idempotency key

Employee Checkin custom-field semantics (simplified from v1):
    whatsapp_sent = 0 / None -> not yet handed to the dispatcher
    whatsapp_sent = 1        -> handed to the dispatcher (delivery status
                               now lives in WhatsApp Send Log, not here)
    whatsapp_sent = 3        -> invalid / missing number — do not retry
    whatsapp_sent = 2        -> RETIRED. "failed, retry transport" is the
                               dispatcher's job now; nothing writes 2.
"""

from __future__ import annotations

import base64
from datetime import timedelta

import frappe
from frappe.utils import format_datetime, get_datetime

from kreativ_notification.notification.dispatcher import dispatch

ATTENDANCE_SHIFT_DOCTYPE = "KG Employee Attendance Shift"


# ---------------------------------------------------------------------------
# Recipient resolution (single source of truth for employee numbers)
# ---------------------------------------------------------------------------

def _employee_recipient(employee: str, settings) -> str | None:
    """Employee.cell_number -> 'CC..........@c.us' or None if unusable."""
    mobile = frappe.db.get_value("Employee", employee, "cell_number") or ""
    digits = "".join(filter(str.isdigit, mobile))
    if len(digits) < 10:
        return None
    cc = "".join(filter(str.isdigit, settings.default_country_code or "91"))
    if cc and len(digits) == 10:
        digits = cc + digits
    return digits + "@c.us"


# ---------------------------------------------------------------------------
# Checkin notifications
# ---------------------------------------------------------------------------

def _get_shift_hours_for_out(employee: str, out_time) -> str:
    """Worked hours string for an OUT punch.

    1. Prefer the paired KG Employee Attendance Shift row (if the async
       recalculation has already run) — guarded, because that doctype
       belongs to kreativ_attendance which may not be installed.
    2. Fall back to the last IN punch before this OUT — simple and free
       of job-ordering races.
    """
    try:
        if frappe.db.exists("DocType", ATTENDANCE_SHIFT_DOCTYPE):
            worked = frappe.db.get_value(
                ATTENDANCE_SHIFT_DOCTYPE,
                {"employee": employee, "check_out": out_time},
                "worked_hours",
            )
            if worked:
                return worked

        last_in = frappe.db.get_value(
            "Employee Checkin",
            filters={
                "employee": employee,
                "log_type": "IN",
                "time": ["<", out_time],
            },
            fieldname="time",
            order_by="time desc",
        )
        if last_in:
            secs = (get_datetime(out_time) - get_datetime(last_in)).total_seconds()
            if 0 < secs < 24 * 3600:
                return f"{int(secs // 3600)}:{int((secs % 3600) // 60):02d}"
    except Exception:
        frappe.log_error(title="Shift hours lookup failed",
                         message=frappe.get_traceback())
    return ""


def notify_checkin(checkin_name: str | frappe.model.document.Document, test_mode: bool = False):
    """Background job: dispatch one WhatsApp message for a new punch.

    Accepts either a checkin name (str) or a document object (from doc_events hook).
    """
    # Handle both string (name) and Document object passed by Frappe hooks
    if hasattr(checkin_name, "name"):
        checkin_name = checkin_name.name

    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.enabled:
        return

    c = frappe.db.get_value(
        "Employee Checkin",
        {"name": checkin_name},
        ["employee", "employee_name", "log_type", "time", "whatsapp_sent"],
        as_dict=True,
    )
    if not c or c.whatsapp_sent in (1, 3):
        return  # already handed off / permanently unroutable

    # Direction filter. v1 returned WITHOUT marking, so the retry cron
    # re-enqueued filtered punches every 10 min for 24h. Mark them handled.
    notify_on = settings.notify_on or "IN and OUT"
    if (notify_on == "IN only" and c.log_type != "IN") or \
       (notify_on == "OUT only" and c.log_type != "OUT"):
        frappe.db.set_value("Employee Checkin", checkin_name,
                            "whatsapp_sent", 1, update_modified=False)
        return

    emoji = "🟢" if c.log_type == "IN" else "🔴"
    text = "{0} {1} - {2} at {3}".format(
        emoji,
        c.log_type,
        c.employee_name or c.employee,
        format_datetime(c.time, "dd-MM-yyyy HH:mm"),
    )
    if c.log_type == "OUT":
        hours = _get_shift_hours_for_out(c.employee, c.time)
        if hours:
            text = f"{text} shift hours: {hours}"

    # Test mode -> route to the admin chat instead of the employee.
    if test_mode or getattr(settings, "test_mode", 0):
        recipient = settings.chat_id
        if not recipient:
            return
    else:
        recipient = _employee_recipient(c.employee, settings)
        if not recipient:
            # No usable number: try admin fallback once, else stop forever.
            if settings.chat_id:
                recipient = settings.chat_id
            else:
                frappe.db.set_value("Employee Checkin", checkin_name,
                                    "whatsapp_sent", 3, update_modified=False)
                frappe.log_error(
                    title=f"Checkin WhatsApp: invalid number for {c.employee}",
                    message=f"Employee {c.employee} ({c.employee_name}) has no "
                            "valid cell_number and no admin fallback chat is "
                            "configured. Marked whatsapp_sent=3 (stop).",
                )
                return

    result = dispatch(
        recipient=recipient,
        text=text,
        message_type="Custom",
        source_doctype="Employee Checkin",
        source_docname=checkin_name,
        priority="Normal",
        # One logical notification per punch, ever — even if this job runs
        # twice (double enqueue, retry cron overlap), dispatch() dedupes.
        idempotency_key=f"checkin:{checkin_name}",
    )

    if result.get("success"):
        frappe.db.set_value("Employee Checkin", checkin_name,
                            "whatsapp_sent", 1, update_modified=False)
    # On dispatch() refusal (e.g. no channel configured) we deliberately
    # leave whatsapp_sent at 0 so the retry cron tries again later.


def retry_missed_notifications():
    """Cron safety net — re-enqueue punches that never REACHED the dispatcher.

    Transport failures are retried by the dispatcher itself (backoff,
    Permanently Failed). This job only catches punches whose original
    notify_checkin enqueue was lost (Redis blip, worker crash before the
    dispatch() call). It is idempotent end-to-end because notify_checkin
    skips whatsapp_sent=1 and dispatch() dedupes on checkin:{name}.
    """
    if not frappe.db.get_single_value("OpenWA Settings", "enabled"):
        return

    cutoff = get_datetime() - timedelta(hours=24)
    unsent = frappe.get_all(
        "Employee Checkin",
        filters={
            "whatsapp_sent": ["in", [0, None]],
            "creation": [">=", cutoff],
        },
        pluck="name",
        order_by="creation asc",
        limit_page_length=50,
    )
    for name in unsent:
        try:
            frappe.enqueue(
                "kreativ_notification.notification.employee_notifications.notify_checkin",
                queue="short",
                timeout=60,
                checkin_name=name,
                job_id=f"notif-checkin-retry-{name}",
                deduplicate=True,
                enqueue_after_commit=False,  # already inside a scheduled job
            )
        except Exception:
            frappe.log_error(
                title=f"WhatsApp retry enqueue failed for {name}",
                message=frappe.get_traceback(),
            )


# ---------------------------------------------------------------------------
# Salary slips
# ---------------------------------------------------------------------------

def send_salary_slip(salary_slip: str):
    """Background job: render the Salary Slip PDF and dispatch it."""
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not (settings.enabled and getattr(settings, "send_salary_slips", 0)):
        return

    slip = frappe.db.get_value(
        "Salary Slip", salary_slip,
        ["employee", "employee_name", "start_date", "docstatus"],
        as_dict=True,
    )
    if not slip or slip.docstatus != 1:
        return

    if getattr(settings, "test_mode", 0):
        recipient = settings.chat_id
    else:
        recipient = _employee_recipient(slip.employee, settings)
    if not recipient:
        frappe.log_error(
            title=f"Salary slip WhatsApp: no recipient for {slip.employee}",
            message=f"{salary_slip}: employee has no valid cell_number "
                    "(and test_mode admin chat not configured).",
        )
        return

    print_format = getattr(settings, "salary_slip_print_format", None) or None
    try:
        pdf_bytes = frappe.get_print(
            "Salary Slip", salary_slip,
            print_format=print_format, as_pdf=True,
        )
        file_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    except Exception:
        frappe.log_error(
            title=f"Salary slip PDF render failed: {salary_slip}",
            message=frappe.get_traceback(),
        )
        return

    period = format_datetime(slip.start_date, "MMMM yyyy") if slip.start_date else ""
    dispatch(
        recipient=recipient,
        text=f"Salary Slip for {period}".strip(),
        file_b64=file_b64,
        filename=f"{salary_slip}.pdf",
        mimetype="application/pdf",
        message_type="Custom",
        source_doctype="Salary Slip",
        source_docname=salary_slip,
        source_print_format=print_format or "",
        # Salary data is sensitive: one send per slip, ever. Cancelling and
        # re-submitting creates a NEW slip name -> new key -> new send.
        idempotency_key=f"salary-slip:{salary_slip}",
        priority="Normal",
    )