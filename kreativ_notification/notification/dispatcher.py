"""Unified send pipeline.

Every outbound message — rule-driven, bot reply, print button, test —
goes through dispatch(). It is the ONLY writer of WhatsApp Send Log and
the only caller of channel drivers.

Guarantees:
    - Idempotency: same idempotency_key never sends twice
    - Outbox: log row is created BEFORE the network call
    - Retry with exponential backoff, terminal "Permanently Failed"
    - Quiet hours + per-channel rate limiting (Urgent bypasses quiet hours)
    - Fallback channel scheduling
    - Circuit breaker counts ONLY transport failures

v3 CHANGES (marked with  # FIX v3):
    1. Circuit breaker no longer trips on PERMANENT failures (bad number,
       unconfigured channel). Three bad numbers in a row used to open the
       breaker and stall the whole channel.
    2. Attachment payload expired from cache -> clear Permanently Failed
       with an explanatory error instead of silently sending text-only.
    3. process_fallbacks: (a) skips rows that are only waiting out quiet
       hours — a deliberately-held message no longer escalates to the
       fallback channel at night; (b) re-attaches the cached file payload
       so a salary-slip PDF escalated to email arrives WITH the PDF.
    4. cleanup_old_logs now also prunes Delivered/Read rows (previously
       only "Sent" — receipt-advanced rows grew forever).

MERGE NOTE: this file was reconstructed from the reviewed sources. Diff
against your current dispatcher.py before replacing — if your deliver()
has extra branches (e.g. Meta template routing details) keep them; the
FIX v3 blocks are the only intended behaviour changes.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_time, now_datetime, nowtime

from kreativ_notification.notification.channels import get_default_channel, get_driver

LOG_DOCTYPE = "WhatsApp Send Log"  # kept for continuity; now channel-aware

MAX_ATTEMPTS = 5
# minutes to wait before retry attempt n (1-indexed)
BACKOFF_MINUTES = [1, 5, 15, 60, 180]

CIRCUIT_THRESHOLD = 3

# Reason strings written by _defer(); process_fallbacks keys off these.
DEFER_QUIET_HOURS = "Quiet hours"
DEFER_RATE_LIMIT = "Rate limit"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def dispatch(
    recipient: str,
    channel: str | None = None,
    text: str = "",
    subject: str = "",
    file_b64: str | None = None,
    filename: str | None = None,
    mimetype: str = "application/pdf",
    message_type: str = "Custom",
    source_doctype: str = "System",
    source_docname: str = "",
    source_print_format: str = "",
    priority: str = "Normal",
    idempotency_key: str | None = None,
    fallback_channel: str | None = None,
    fallback_after_minutes: int = 30,
    meta_template_name: str | None = None,
    meta_template_language: str = "en",
    rule: str | None = None,
) -> dict:
    """Create a Send Log entry and enqueue delivery. Returns immediately.

    Never raises for delivery problems — check the log.
    """
    channel = channel or get_default_channel()
    if not channel:
        return {"success": False, "error": _("No enabled Notification Channel configured.")}

    # ---- Idempotency: refuse to create a duplicate logical send ----------
    if idempotency_key:
        existing = frappe.db.get_value(
            LOG_DOCTYPE, {"idempotency_key": idempotency_key},
            ["name", "status"], as_dict=True,
        )
        if existing and existing["status"] not in ("Failed",):
            return {"success": True, "status": "duplicate",
                    "log_name": existing["name"],
                    "message": _("Already sent/queued (idempotent).")}

    log = frappe.get_doc({
        "doctype": LOG_DOCTYPE,
        "source_doctype": source_doctype,
        "source_docname": source_docname,
        "recipient": recipient,
        "recipient_display": recipient,
        "message_type": message_type,
        "source_print_format": source_print_format,
        "status": "Queued",
        "channel": channel,
        "priority": priority,
        "idempotency_key": idempotency_key,
        "fallback_channel": fallback_channel,
        "fallback_deadline": (
            add_to_date(now_datetime(), minutes=cint(fallback_after_minutes))
            if fallback_channel else None
        ),
        "notification_rule": rule,
    })
    log.insert(ignore_permissions=True)
    # Use db_set for 'meta' field to avoid conflict with Document.meta property
    frappe.db.set_value(LOG_DOCTYPE, log.name, "meta", frappe.as_json({
        "text": text,
        "subject": subject,
        "filename": filename,
        "mimetype": mimetype,
        "has_file": bool(file_b64),
        "meta_template_name": meta_template_name,
        "meta_template_language": meta_template_language,
    }))
    frappe.db.commit()

    # Payload (incl. base64 file) goes to cache, not the DB row
    if file_b64:
        frappe.cache().set_value(_payload_key(log.name), file_b64,
                                 expires_in_sec=6 * 3600)

    _enqueue_delivery(log.name, priority)
    return {"success": True, "status": "queued", "log_name": log.name}


def _payload_key(log_name: str) -> str:
    return f"notif_payload:{frappe.local.site}:{log_name}"


def _enqueue_delivery(log_name: str, priority: str = "Normal"):
    queue = {"Urgent": "short", "Normal": "long", "Bulk": "long"}.get(priority, "long")
    frappe.enqueue(
        "kreativ_notification.notification.dispatcher.deliver",
        queue=queue,
        timeout=600,
        job_id=f"notif-deliver-{log_name}",
        enqueue_after_commit=True,
        log_name=log_name,
    )


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def deliver(log_name: str):
    """Background worker: claim the log row, run the driver, record result."""
    # Atomic claim — prevents double delivery if enqueued twice
    frappe.db.sql(
        f"""UPDATE `tab{LOG_DOCTYPE}`
            SET status = 'Processing'
            WHERE name = %s AND status = 'Queued'""",
        (log_name,),
    )
    frappe.db.commit()
    row = frappe.db.get_value(LOG_DOCTYPE, log_name,
                              ["status", "channel", "recipient", "meta",
                               "retry_count", "priority"], as_dict=True)
    if not row or row["status"] != "Processing":
        return  # someone else claimed it, or it's already terminal

    meta = json.loads(row["meta"] or "{}")
    channel = row["channel"]

    # ---- Circuit breaker (transport-level only) --------------------------
    if _breaker_open(channel):
        _reschedule(log_name, row["retry_count"],
                    error="Circuit breaker open — channel failing", count_attempt=False)
        return

    # ---- Quiet hours (Urgent bypasses) -----------------------------------
    if row["priority"] != "Urgent":
        wait_min = _quiet_hours_wait(channel)
        if wait_min:
            _defer(log_name, minutes=wait_min, reason=DEFER_QUIET_HOURS)
            return

    # ---- Per-channel rate limit ------------------------------------------
    if not _rate_limit_ok(channel):
        _defer(log_name, minutes=1, reason=DEFER_RATE_LIMIT)
        return

    # ---- Resolve driver + recipient --------------------------------------
    try:
        driver = get_driver(channel)
    except Exception as e:
        _finalize(log_name, False, f"Driver error: {e}", permanent=True)
        return

    _normalized = driver.normalize_recipient(row["recipient"])
    if not _normalized:
        _finalize(log_name, False,
                  f"Invalid recipient for {driver.driver_type}: {row['recipient']}",
                  permanent=True)
        return

    # ---- Send ------------------------------------------------------------
    file_b64 = frappe.cache().get_value(_payload_key(log_name)) if meta.get("has_file") else None

    # FIX v3: cache expired -> permanent failure with clear error
    if meta.get("has_file") and not file_b64:
        _finalize(log_name, False,
                  "Attachment expired from cache (6h TTL). Re-queue the send.",
                  permanent=True)
        return

    try:
        if meta.get("meta_template_name") and driver.supports_templates:
            result = driver.send_template(
                _normalized, meta["meta_template_name"],
                meta.get("meta_template_language") or "en",
            )
        elif file_b64:
            result = driver.send_document(
                _normalized, file_b64,
                meta.get("filename") or "document.pdf",
                mimetype=meta.get("mimetype") or "application/pdf",
                caption=meta.get("text") or "",
                subject=meta.get("subject") or "",
            )
        else:
            result = driver.send_text(_normalized, meta.get("text") or "",
                                      subject=meta.get("subject") or "")
    except Exception as e:
        frappe.log_error(title=f"Driver crashed: {channel}",
                         message=frappe.get_traceback())
        result = {"success": False, "error": str(e), "permanent": False}

    # ---- Record ----------------------------------------------------------
    if result.get("success"):
        _breaker_reset(channel)
        frappe.db.set_value(LOG_DOCTYPE, log_name, {
            "status": "Sent",
            "provider_message_id": result.get("message_id") or "",
            "error_message": "",
        }, update_modified=False)
        frappe.db.commit()
        frappe.cache().delete_value(_payload_key(log_name))
    else:
        # FIX v3: only trip breaker on TRANSPORT failures (non-permanent)
        if result.get("permanent"):
            # Bad number, unconfigured channel etc. — do NOT open breaker
            _finalize(log_name, False, result.get("error"), permanent=True)
        else:
            _breaker_trip(channel)
            _reschedule(log_name, row["retry_count"], error=result.get("error"))


# ---------------------------------------------------------------------------
# Retry / defer / finalize
# ---------------------------------------------------------------------------

def _reschedule(log_name: str, retry_count: int, error: str = "",
                count_attempt: bool = True):
    attempts = cint(retry_count) + (1 if count_attempt else 0)
    if attempts >= MAX_ATTEMPTS:
        _finalize(log_name, False,
                  f"{error} (gave up after {attempts} attempts)", permanent=True)
        return
    wait = BACKOFF_MINUTES[min(attempts, len(BACKOFF_MINUTES) - 1)]
    frappe.db.set_value(LOG_DOCTYPE, log_name, {
        "status": "Queued",
        "retry_count": attempts,
        "retry_after": add_to_date(now_datetime(), minutes=wait),
        "error_message": (error or "")[:1000],
    }, update_modified=False)
    frappe.db.commit()


def _defer(log_name: str, minutes: int, reason: str):
    """Push back without consuming a retry attempt."""
    frappe.db.set_value(LOG_DOCTYPE, log_name, {
        "status": "Queued",
        "retry_after": add_to_date(now_datetime(), minutes=minutes),
        "error_message": reason,
    }, update_modified=False)
    frappe.db.commit()


def _finalize(log_name: str, success: bool, error: str = "", permanent: bool = False):
    status = "Sent" if success else ("Permanently Failed" if permanent else "Failed")
    frappe.db.set_value(LOG_DOCTYPE, log_name, {
        "status": status,
        "error_message": (error or "")[:1000],
    }, update_modified=False)
    frappe.db.commit()
    frappe.cache().delete_value(_payload_key(log_name))


# ---------------------------------------------------------------------------
# Scheduler entry points
# ---------------------------------------------------------------------------

def process_due_retries():
    """Cron (every few minutes): re-enqueue Queued rows whose retry_after passed."""
    due = frappe.get_all(
        LOG_DOCTYPE,
        filters={"status": "Queued",
                 "retry_after": ["<=", now_datetime()],
                 "channel": ["is", "set"]},
        or_filters=None,
        fields=["name", "priority"],
        order_by="creation asc",
        limit_page_length=100,
    )
    # Rows with no retry_after set were enqueued directly at dispatch time;
    # this loop is the safety net that also picks up any that slipped through.
    fresh = frappe.get_all(
        LOG_DOCTYPE,
        filters={"status": "Queued", "retry_after": ["is", "not set"],
                 "creation": ["<", add_to_date(now_datetime(), minutes=-10)],
                 "channel": ["is", "set"]},
        fields=["name", "priority"],
        limit_page_length=100,
    )
    for row in due + fresh:
        _enqueue_delivery(row["name"], row["priority"] or "Normal")


def process_fallbacks():
    """Cron: fire fallback channel for messages not Sent/Delivered in time."""
    # FIX v3: skip rows whose ONLY problem is quiet-hours deferral. A message
    # deliberately held until morning should not silently escalate at 02:00.
    overdue = frappe.get_all(
        LOG_DOCTYPE,
        filters={
            "fallback_channel": ["is", "set"],
            "fallback_fired": 0,
            "fallback_deadline": ["<=", now_datetime()],
            "status": ["in", ["Queued", "Processing", "Failed"]],
            "error_message": ["!=", DEFER_QUIET_HOURS],  # FIX v3
        },
        fields=["name", "fallback_channel", "recipient", "meta",
                "source_doctype", "source_docname", "message_type",
                "priority", "notification_rule"],
        limit_page_length=50,
    )
    for row in overdue:
        meta = json.loads(row["meta"] or "{}")
        frappe.db.set_value(LOG_DOCTYPE, row["name"], "fallback_fired", 1,
                            update_modified=False)

        # FIX v3: re-attach cached file payload so PDF escalates with attachment
        file_b64 = None
        if meta.get("has_file"):
            file_b64 = frappe.cache().get_value(_payload_key(row["name"]))

        dispatch(
            recipient=row["recipient"],
            channel=row["fallback_channel"],
            text=meta.get("text") or "",
            subject=meta.get("subject") or "",
            file_b64=file_b64,
            filename=meta.get("filename"),
            mimetype=meta.get("mimetype"),
            message_type=row["message_type"],
            source_doctype=row["source_doctype"],
            source_docname=row["source_docname"],
            priority=row["priority"] or "Normal",
            idempotency_key=None,  # a fallback is a NEW logical send
            rule=row["notification_rule"],
        )
    if overdue:
        frappe.db.commit()


def cleanup_old_logs(days_sent: int = 90, days_failed: int = 180):
    """Daily: prune old logs so the table doesn't grow forever.

    FIX v3: also prune Delivered/Read rows (they used to accumulate forever).
    """
    frappe.db.delete(LOG_DOCTYPE, {
        "status": ["in", ["Sent", "Delivered", "Read"]],
        "creation": ["<", add_to_date(now_datetime(), days=-days_sent)],
    })
    frappe.db.delete(LOG_DOCTYPE, {
        "status": ["in", ["Failed", "Permanently Failed"]],
        "creation": ["<", add_to_date(now_datetime(), days=-days_failed)],
    })
    frappe.db.commit()


# ---------------------------------------------------------------------------
# Delivery receipts (called by inbound webhook parsing)
# ---------------------------------------------------------------------------

def record_delivery_status(provider_message_id: str, status: str):
    """Advance Sent → Delivered → Read from provider status webhooks."""
    if not provider_message_id:
        return
    name = frappe.db.get_value(LOG_DOCTYPE,
                               {"provider_message_id": provider_message_id}, "name")
    if not name:
        return
    mapped = {"delivered": "Delivered", "read": "Read", "failed": "Failed"}.get(status)
    if mapped:
        frappe.db.set_value(LOG_DOCTYPE, name, "status", mapped,
                            update_modified=False)
        frappe.db.commit()


def sync_delivery_status():
    """Cron (every 5 min): sync Delivered/Read status back to Employee Checkin.

    Only runs for WhatsApp - OpenWA channel, Employee Checkin source.
    """
    try:
        sent_logs = frappe.get_all(LOG_DOCTYPE,
            filters={
                "status": ["in", ["Sent", "Delivered", "Read"]],
                "source_doctype": "Employee Checkin",
                "channel": "Primary WhatsApp",
                "delivery_synced": 0
            },
            fields=["name", "source_docname", "status"])
    except Exception:
        # If delivery_synced field doesn't exist yet, skip silently
        return

    for log in sent_logs:
        try:
            # Map status to checkin value: 0=Queued, 1=Queued, 2=Delivered, 3=Failed
            if log["status"] in ("Sent", "Delivered", "Read"):
                frappe.db.set_value("Employee Checkin", log["source_docname"],
                                    "whatsapp_sent", 2, update_modified=False)
            frappe.db.set_value(LOG_DOCTYPE, log["name"],
                                "delivery_synced", 1, update_modified=False)
        except Exception:
            frappe.log_error(
                title=f"Delivery sync failed for {log['name']}",
                message=frappe.get_traceback())

    if sent_logs:
        frappe.db.commit()


# ---------------------------------------------------------------------------
# Quiet hours / rate limit / circuit breaker helpers
# ---------------------------------------------------------------------------

def _quiet_hours_wait(channel: str) -> int:
    ch = frappe.get_cached_doc("Notification Channel", channel)
    if not (ch.quiet_hours_start and ch.quiet_hours_end):
        return 0
    def _as_time(val):
        # Time fields arrive as str ("HH:MM:SS") or datetime.timedelta
        # depending on how the doc was loaded — normalise via str()
        return get_time(str(val))

    now_t = get_time(nowtime())
    start, end = _as_time(ch.quiet_hours_start), _as_time(ch.quiet_hours_end)

    def minutes_until(t):
        return ((t.hour - now_t.hour) * 60 + (t.minute - now_t.minute)) % (24 * 60)

    if start <= end:
        in_quiet = start <= now_t < end
    else:  # spans midnight, e.g. 21:00 → 08:00
        in_quiet = now_t >= start or now_t < end
    return minutes_until(end) or 1 if in_quiet else 0


def _rate_limit_ok(channel: str) -> bool:
    ch = frappe.get_cached_doc("Notification Channel", channel)
    limit = cint(ch.rate_limit_per_minute)
    if not limit:
        return True
    key = f"notif_rate:{frappe.local.site}:{channel}"
    try:
        # Atomic under Redis: INCRBY, first writer opens the 60s window.
        current = frappe.cache().incrby(key, 1)
        if current == 1:
            frappe.cache().expire(key, 60)
        return current <= limit
    except Exception:
        # Cache backend without incrby — fall back to the old (non-atomic)
        # check rather than blocking sends.
        current = cint(frappe.cache().get_value(key) or 0)
        if current >= limit:
            return False
        frappe.cache().set_value(key, current + 1, expires_in_sec=60)
        return True


def _breaker_cache_key(channel: str) -> str:
    return f"notif_breaker:{frappe.local.site}:{channel}"


def _breaker_open(channel: str) -> bool:
    return cint(frappe.cache().get_value(_breaker_cache_key(channel)) or 0) >= CIRCUIT_THRESHOLD


def _breaker_trip(channel: str):
    key = _breaker_cache_key(channel)
    streak = cint(frappe.cache().get_value(key) or 0) + 1
    frappe.cache().set_value(key, streak, expires_in_sec=1800)  # auto-heal in 30 min


def _breaker_reset(channel: str):
    frappe.cache().delete_value(_breaker_cache_key(channel))