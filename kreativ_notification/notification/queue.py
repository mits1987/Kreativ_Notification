"""Queue management for Notification Queue doctype.

v3 — DEDUPLICATED. Notification Queue used to be a second, competing
pipeline: process_send() called the legacy send_* helpers with NO
idempotency key, and retry_failed() re-queued failed items up to 3 times.
One queue item could therefore produce up to 4 independent Send Log rows
and 4 real WhatsApp sends — each of which ALSO got the dispatcher's own
5-attempt retry ladder (retry-inside-retry).

Now every queue item maps to exactly ONE logical send:

    idempotency_key = f"queue:{queue_item}"

The dispatcher dedupes on that key while the send is Queued / Processing /
Sent, and only allows a re-dispatch after a genuine "Failed" — so
retry_failed() becomes safe: it can re-queue as often as it likes without
ever multiplying deliveries.

DEPRECATION NOTE: this doctype is now a thin shim over dispatch(). New
code should call dispatcher.dispatch() directly. Once nothing writes to
Notification Queue any more, delete this module, the doctype, and any
scheduler entries pointing here.
"""
import json

import frappe
from frappe import _

from kreativ_notification.notification.dispatcher import dispatch


def flush_outgoing():
    """Scheduler job: drain queued sends from Notification Queue."""
    try:
        queue_items = frappe.get_all(
            "Notification Queue",
            filters={"status": "Queued"},
            fields=["name"],
            order_by="creation asc",
            limit_page_length=50,
        )

        if not queue_items:
            return {"processed": 0, "message": "No queued items"}

        processed = 0
        for item in queue_items:
            try:
                frappe.enqueue(
                    "kreativ_notification.notification.queue.process_send",
                    queue="long",
                    timeout=300,
                    # job_id dedupes concurrent enqueues of the same item
                    job_id=f"notif-queue-{item.name}",
                    queue_item=item.name,
                )
                processed += 1
            except Exception:
                frappe.log_error(
                    title=f"Queue flush failed for {item.name}",
                    message=frappe.get_traceback(),
                )

        return {"processed": processed, "message": f"Enqueued {processed} items"}

    except Exception:
        frappe.log_error(title="Queue flush error", message=frappe.get_traceback())
        return {"processed": 0, "error": "Queue flush failed"}


def retry_failed():
    """Scheduler job: retry failed sends from Notification Queue.

    Safe now: re-dispatching the same queue item reuses the idempotency
    key queue:{name}, so the dispatcher refuses to create a duplicate
    unless the previous logical send actually terminated as Failed.
    """
    try:
        failed_items = frappe.get_all(
            "Notification Queue",
            filters={"status": "Failed"},
            fields=["name", "attempts"],
            order_by="creation asc",
            limit_page_length=20,
        )

        if not failed_items:
            return {"retried": 0, "message": "No failed items"}

        retried = 0
        for item in failed_items:
            if (item.attempts or 0) >= 3:
                continue
            try:
                frappe.db.set_value(
                    "Notification Queue", item.name,
                    {"status": "Queued", "attempts": (item.attempts or 0) + 1},
                )
                frappe.db.commit()
                retried += 1
            except Exception:
                pass

        return {"retried": retried, "message": f"Re-queued {retried} failed items"}

    except Exception:
        frappe.log_error(title="Queue retry error", message=frappe.get_traceback())
        return {"retried": 0, "error": "Queue retry failed"}


def process_send(queue_item: str):
    """Background worker: hand a single Notification Queue item to dispatch().

    The dispatcher owns transport, retries, rate limits, quiet hours and
    the circuit breaker. This function only translates the queue row into
    a dispatch() call with a stable idempotency key.
    """
    try:
        doc = frappe.get_doc("Notification Queue", queue_item)
        if doc.status not in ("Queued",):
            return  # already handled

        doc.status = "Processing"
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        try:
            payload = json.loads(doc.payload or "{}")
        except (ValueError, TypeError):
            payload = {}

        idem = f"queue:{queue_item}"
        common = dict(
            recipient=doc.recipient,
            source_doctype=doc.reference_doctype or "System",
            source_docname=doc.reference_docname or "",
            priority="Normal",
            idempotency_key=idem,
        )

        if doc.action_type == "send_pdf":
            result = dispatch(
                text=payload.get("caption") or payload.get("filename") or "",
                file_b64=payload.get("file_b64"),
                filename=payload.get("filename") or "document.pdf",
                mimetype="application/pdf",
                message_type="Print PDF",
                source_print_format=payload.get("print_format") or "",
                **common,
            )
        elif doc.action_type == "send_screenshot":
            result = dispatch(
                text=payload.get("caption") or payload.get("filename") or "",
                file_b64=payload.get("file_b64"),
                filename=payload.get("filename") or "screenshot.png",
                mimetype="image/png",
                message_type="Screenshot",
                **common,
            )
        elif doc.action_type == "send_test":
            result = dispatch(
                text=payload.get("text") or "Test message from Kreativ Notification.",
                message_type="Test",
                **common,
            )
        else:  # send_manual / plain text
            result = dispatch(
                text=payload.get("text") or "",
                message_type="Custom",
                **common,
            )

        if result.get("success"):
            # "Sent" here means "accepted by the dispatcher" — final
            # transport status lives in WhatsApp Send Log (result log_name).
            doc.status = "Sent"
            doc.error_message = ""
        else:
            doc.status = "Failed"
            doc.error_message = result.get("error", "Unknown error")

        doc.save(ignore_permissions=True)
        frappe.db.commit()

    except Exception:
        frappe.log_error(title=f"Queue process failed for {queue_item}",
                         message=frappe.get_traceback())
        try:
            doc = frappe.get_doc("Notification Queue", queue_item)
            doc.status = "Failed"
            doc.error_message = frappe.get_traceback()[:500]
            doc.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            pass