"""Rules engine.

One generic handler wired to doc_events["*"] evaluates Notification Rules,
renders the template, and hands everything to the dispatcher. Date-based
rules (Days Before / Days After) run from a daily scheduler job.

Idempotency keys make every (rule, doc, event/date) fire at most once,
so re-saving a document or re-running the daily job can't double-send.
"""

from __future__ import annotations

import base64

import frappe
from frappe.utils import add_days, get_datetime, nowdate

from kreativ_notification.notification.dispatcher import dispatch

EVENT_MAP = {
    "after_insert": "New",
    "on_submit": "Submit",
    "on_cancel": "Cancel",
    "on_update": "Value Change",
    "on_update_after_submit": "Value Change",
}

def _get_shift_hours_for_out(employee: str, out_time) -> str:
    """Worked hours string for an OUT punch (same logic as employee_notifications)."""
    try:
        ATTENDANCE_SHIFT_DOCTYPE = "KG Employee Attendance Shift"
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

SKIP_DOCTYPES = {
    # Never rule-match on our own machinery or high-churn system doctypes
    "WhatsApp Send Log", "Notification Channel", "Notification Rule",
    "Message Template", "Version", "Comment", "Error Log", "Activity Log",
    "Scheduled Job Log", "Email Queue", "Route History", "View Log",
    "Notification Queue",
}


# ---------------------------------------------------------------------------
# doc_events["*"] entry point
# ---------------------------------------------------------------------------

def handle_doc_event(doc, method=None):
    """Wired in hooks.py for after_insert / on_submit / on_cancel /
    on_update / on_update_after_submit on every doctype."""
    try:
        if doc.doctype in SKIP_DOCTYPES or getattr(doc.flags, "in_migrate", False):
            return
        if frappe.flags.in_install or frappe.flags.in_patch or frappe.flags.in_import:
            return

        event = EVENT_MAP.get(method)
        if not event:
            return

        rules = _get_rules(doc.doctype, event)
        if not rules:
            return

        for rule_name in rules:
            _process_rule(rule_name, doc, event)
    except Exception:
        # A notification must NEVER break a business transaction
        frappe.log_error(title="Rules engine error",
                         message=frappe.get_traceback())


def _get_rules(doctype: str, event: str) -> list[str]:
    """Cached lookup so the * hook adds ~0 cost for doctypes without rules."""
    cache_key = f"notif_rules:{doctype}:{event}"
    cached = frappe.cache().get_value(cache_key)
    if cached is not None:
        return cached
    rules = frappe.get_all(
        "Notification Rule",
        filters={"enabled": 1, "document_type": doctype, "event": event},
        pluck="name",
    )
    frappe.cache().set_value(cache_key, rules, expires_in_sec=300)
    return rules


def clear_rule_cache(doc=None, method=None):
    """on_update/on_trash of Notification Rule → invalidate lookup cache."""
    frappe.cache().delete_keys("notif_rules:")


# ---------------------------------------------------------------------------
# Rule processing
# ---------------------------------------------------------------------------

def _process_rule(rule_name: str, doc, event: str, date_key: str = ""):
    rule = frappe.get_cached_doc("Notification Rule", rule_name)

    # Value Change: only fire when the watched field actually changed
    if rule.event == "Value Change" and event == "Value Change":
        if not rule.value_changed_field:
            return
        if not doc.has_value_changed(rule.value_changed_field):
            return

    if not rule.applies_to(doc):
        return

    recipients = rule.resolve_recipients(doc)
    if not recipients:
        return

    template = frappe.get_cached_doc("Message Template", rule.message_template)
    if not template.enabled:
        return

    language = rule.get_recipient_language(doc)

    # Compute shift_hours for Employee Checkin OUT punches (template expects doc.shift_hours)
    if doc.doctype == "Employee Checkin" and doc.log_type == "OUT" and doc.employee:
        doc.shift_hours = _get_shift_hours_for_out(doc.employee, doc.time)
    else:
        doc.shift_hours = ""

    rendered = template.render(doc, language)

    # Attachment (PDF) rendered once, shared by all recipients
    file_b64, filename = None, None
    if template.attach_print:
        file_b64, filename = _render_pdf(doc, template)

    # Value Change idempotency includes the new value so later changes re-fire
    change_part = ""
    if rule.event == "Value Change":
        change_part = f":{doc.get(rule.value_changed_field)}"

    for recipient in recipients:
        idem = f"rule:{rule.name}:{doc.doctype}:{doc.name}:{event}{change_part}{date_key}:{recipient}"
        dispatch(
            recipient=recipient,
            channel=rule.channel or None,
            text=rendered["body"],
            subject=rendered["subject"],
            file_b64=file_b64,
            filename=filename,
            message_type="Rule",
            source_doctype=doc.doctype,
            source_docname=doc.name,
            source_print_format=template.print_format or "",
            priority=rule.priority or "Normal",
            idempotency_key=idem,
            fallback_channel=rule.fallback_channel or None,
            fallback_after_minutes=rule.fallback_after_minutes or 30,
            meta_template_name=template.meta_template_name or None,
            meta_template_language=template.meta_template_language or "en",
            rule=rule.name,
        )


def _render_pdf(doc, template) -> tuple[str | None, str | None]:
    try:
        pdf_bytes = frappe.get_print(
            doc.doctype, doc.name,
            print_format=template.print_format or None,
            as_pdf=True,
        )
        return (base64.b64encode(pdf_bytes).decode("utf-8"),
                template.render_attachment_filename(doc))
    except Exception:
        frappe.log_error(title=f"PDF render failed for {doc.doctype} {doc.name}",
                         message=frappe.get_traceback())
        return None, None


# ---------------------------------------------------------------------------
# Date-based rules (Days Before / Days After) — daily scheduler job
# ---------------------------------------------------------------------------

def evaluate_date_rules():
    """Daily cron. For each Days Before/After rule, find documents whose
    date_field lands exactly `days_offset` days from today and fire."""
    rules = frappe.get_all(
        "Notification Rule",
        filters={"enabled": 1, "event": ["in", ["Days Before", "Days After"]]},
        fields=["name", "document_type", "event", "date_field", "days_offset"],
    )
    today = nowdate()

    for r in rules:
        if not r.date_field:
            continue
        try:
            meta = frappe.get_meta(r.document_type)
            if not meta.has_field(r.date_field):
                continue

            # Days Before due_date=today+offset ; Days After due_date=today-offset
            offset = r.days_offset or 0
            target = add_days(today, offset if r.event == "Days Before" else -offset)

            filters = {r.date_field: target}
            if meta.is_submittable:
                filters["docstatus"] = 1

            # FIX: paginated loop — the old single get_all(limit_page_length=500)
            # silently capped at 500 docs. Idempotency keys prevent double-fires
            # if the job reruns.
            start = 0
            while True:
                names = frappe.get_all(
                    r.document_type, filters=filters, pluck="name",
                    limit_start=start, limit_page_length=500,
                )
                if not names:
                    break
                for name in names:
                    doc = frappe.get_doc(r.document_type, name)
                    _process_rule(r.name, doc, r.event, date_key=f":{today}")
                if len(names) < 500:
                    break
                start += 500
        except Exception:
            frappe.log_error(title=f"Date rule failed: {r.name}",
                             message=frappe.get_traceback())
