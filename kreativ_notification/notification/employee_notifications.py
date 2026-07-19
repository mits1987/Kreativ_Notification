"""Employee checkin and salary slip WhatsApp notifications."""
import frappe
import re
import base64
from frappe.utils import format_datetime, get_datetime
from datetime import datetime, timedelta
from kreativ_notification.notification.openwa_client import OpenWAClient, _breaker_key, _get_failure_streak

MAX_RETRY_ATTEMPTS = 5
ADMIN_CHAT_ID = "919106526195@c.us"


def _get_shift_hours_for_out(employee: str, checkin_time: datetime) -> str:
    try:
        checkin_date = checkin_time.date()
        shift = frappe.db.get_value(
            "Employee Shift",
            {"employee": employee, "start_date": checkin_date},
            "worked_hours",
        )
        if shift:
            return shift

        prev_date = checkin_date - timedelta(days=1)
        shift = frappe.db.get_value(
            "Employee Shift",
            {"employee": employee, "start_date": prev_date},
            "worked_hours",
        )
        if shift:
            return shift

        last_in = frappe.db.get_value(
            "Employee Checkin",
            {"employee": employee, "log_type": "IN", "time": ["<", checkin_time]},
            "time",
            order_by="time desc",
        )
        if last_in:
            if isinstance(last_in, str):
                last_in = get_datetime(last_in)
            total_seconds = int((checkin_time - last_in).total_seconds())
            if total_seconds > 0:
                hours = int(total_seconds // 3600)
                minutes = int((total_seconds % 3600) // 60)
                return f"{hours}:{minutes:02d}"

        return ""
    except Exception:
        return ""


def _mark_failed(checkin_name: str, retry_count: int):
    if retry_count >= MAX_RETRY_ATTEMPTS:
        frappe.db.set_value(
            "Employee Checkin", checkin_name,
            {"whatsapp_sent": 3, "whatsapp_retry_count": retry_count},
            update_modified=False,
        )
        frappe.get_doc({
            "doctype": "Comment",
            "comment_type": "Info",
            "reference_doctype": "Employee Checkin",
            "reference_name": checkin_name,
            "content": (
                f"WhatsApp permanently failed after {retry_count} attempts. "
                f"Possible causes: invalid phone number, employee not on WhatsApp, "
                f"or OpenWA cannot reach this contact. Stopped retrying to save resources."
            ),
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        return 3
    else:
        frappe.db.set_value(
            "Employee Checkin", checkin_name,
            {"whatsapp_sent": 2, "whatsapp_retry_count": retry_count},
            update_modified=False,
        )
        frappe.db.commit()
        return 2


def notify_checkin(checkin_name: str, test_mode: bool = False):
    """Background job: send one WhatsApp message for a new punch."""
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not (settings.enabled and settings.base_url):
        return

    from kreativ_notification.notification.health import _can_attempt_probe
    if not _can_attempt_probe():
        return

    c = frappe.db.get_value(
        "Employee Checkin", checkin_name,
        ["employee", "employee_name", "log_type", "time",
         "whatsapp_sent", "whatsapp_retry_count"],
        as_dict=True,
    )
    if not c:
        return

    if c.whatsapp_sent in (1, 3):
        return

    if settings.notify_on == "IN only" and c.log_type != "IN":
        return
    if settings.notify_on == "OUT only" and c.log_type != "OUT":
        return

    icon = "IN" if c.log_type == "IN" else "OUT"
    text = "{0} - {1} at {2}".format(
        icon,
        c.employee_name or c.employee,
        format_datetime(c.time, "dd-MM-yyyy HH:mm"),
    )

    if c.log_type == "OUT":
        shift_hours = _get_shift_hours_for_out(c.employee, c.time)
        if shift_hours:
            text = "{0} shift hours: {1}".format(text, shift_hours)

    if test_mode or settings.test_mode:
        _post(settings, "send-text", {"chatId": ADMIN_CHAT_ID, "text": text})
        return

    retry_count = (c.whatsapp_retry_count or 0) + 1

    mobile = frappe.db.get_value("Employee", c.employee, "cell_number") or ""
    digits = "".join(filter(str.isdigit, mobile))

    if len(digits) >= 10:
        cc = "".join(filter(str.isdigit, settings.default_country_code or ""))
        if cc and not digits.startswith(cc) and len(digits) <= 10:
            digits = cc + digits

        chat_id = digits + "@c.us"

        if _post(settings, "send-text", {"chatId": chat_id, "text": text}):
            frappe.db.set_value(
                "Employee Checkin", checkin_name,
                {"whatsapp_sent": 1, "whatsapp_retry_count": retry_count},
                update_modified=False,
            )
            frappe.db.commit()
        else:
            _mark_failed(checkin_name, retry_count)
    else:
        if settings.chat_id:
            if _post(settings, "send-text", {"chatId": settings.chat_id, "text": text}):
                frappe.db.set_value(
                    "Employee Checkin", checkin_name,
                    {"whatsapp_sent": 1, "whatsapp_retry_count": retry_count},
                    update_modified=False,
                )
                frappe.db.commit()
            else:
                _mark_failed(checkin_name, retry_count)


def send_salary_slip(salary_slip: str):
    """Background job: render the Salary Slip PDF and WhatsApp it."""
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not (settings.enabled and settings.send_salary_slips and settings.base_url):
        return

    slip = frappe.get_doc("Salary Slip", salary_slip)
    mobile = frappe.db.get_value("Employee", slip.employee, "cell_number") or ""
    digits = re.sub(r"\D", "", mobile)
    if not digits:
        frappe.log_error(
            title="Salary slip WhatsApp skipped: no mobile number",
            message=f"{slip.employee} ({slip.employee_name}) has no cell_number on the Employee record.",
        )
        return
    cc = re.sub(r"\D", "", settings.default_country_code or "")
    if cc and not digits.startswith(cc) and len(digits) <= 10:
        digits = cc + digits

    pdf = frappe.get_print(
        "Salary Slip", slip.name,
        print_format=settings.salary_slip_print_format or None,
        as_pdf=True,
    )
    period = frappe.utils.format_date(slip.start_date, "MMMM yyyy")
    filename = f"Salary Slip {period} - {slip.employee_name}.pdf"
    caption = f"Salary Slip - {period}"

    client = OpenWAClient()
    client.send_document(
        chat_id=f"{digits}@c.us",
        base64_data=base64.b64encode(pdf).decode(),
        filename=filename,
        mimetype="application/pdf",
        caption=caption,
    )


def _post(settings, endpoint: str, payload: dict, raise_on_error: bool = False):
    chat_id = payload.get("chatId", settings.chat_id)
    text = payload.get("text", "")

    client = OpenWAClient()
    try:
        if endpoint == "send-text":
            result = client.send_text(chat_id, text)
        elif endpoint == "send-document":
            result = client.send_document(chat_id, payload.get("base64", ""),
                                          payload.get("filename", "document"),
                                          mimetype=payload.get("mimetype", "application/pdf"),
                                          caption=payload.get("caption", ""))
        else:
            return False

        if result.get("success"):
            return True
        frappe.log_error(title="OpenWA send failed", message=result.get("error", "Unknown error"))
        if raise_on_error:
            frappe.throw(result.get("error", "Could not send via OpenWA"))
        return False
    except Exception:
        frappe.log_error(title="OpenWA WhatsApp send failed", message=frappe.get_traceback())
        if raise_on_error:
            frappe.throw("Could not send via OpenWA")
        return False


def retry_missed_notifications():
    """Scheduled job: find Employee Checkins where whatsapp_sent was never set to 1 and retry."""
    settings = frappe.get_single("OpenWA Settings")
    if not (settings.enabled and settings.base_url):
        return

    if frappe.cache().get_value(_breaker_key("stale")):
        frappe.log_error(
            title="OpenWA Retry Skipped",
            message="Session is stale (lastActive > 60 min).",
        )
        return

    perm_fail_cutoff = get_datetime() - timedelta(days=7)
    if _get_failure_streak() < 3:
        frappe.db.sql(
            """
            UPDATE `tabEmployee Checkin`
            SET whatsapp_sent = 0
            WHERE whatsapp_sent = 2
              AND creation >= %s
            """,
            (perm_fail_cutoff,),
        )
        frappe.db.commit()

    cutoff = get_datetime() - timedelta(hours=24)
    unsent = frappe.get_all(
        "Employee Checkin",
        filters={
            "whatsapp_sent": ["in", [0, None]],
            "creation": [">=", cutoff],
        },
        fields=["name", "employee_name", "log_type", "creation"],
        order_by="creation asc",
        limit_page_length=50,
    )

    if not unsent:
        return

    enqueued = 0
    for c in unsent:
        try:
            frappe.enqueue(
                "kreativ_notification.notification.employee_notifications.notify_checkin",
                queue="short",
                timeout=60,
                checkin_name=c.name,
                enqueue_after_commit=False,
            )
            enqueued += 1
        except Exception:
            frappe.log_error(
                title=f"WhatsApp retry enqueue failed for {c.name}",
                message=frappe.get_traceback(),
            )

    if enqueued:
        frappe.logger().info(
            f"WhatsApp retry: enqueued {enqueued}/{len(unsent)} missed notifications"
        )