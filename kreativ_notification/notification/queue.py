"""Queue management for Notification Queue doctype."""
import frappe
from frappe import _


def flush_outgoing():
    """ =ALL= job: drain queued sends from Notification Queue."""
    try:
        queue_items = frappe.get_all(
            "Notification Queue",
            filters={"status": "Queued"},
            fields=["name", "action_type", "reference_doctype", "reference_docname", "recipient", "payload"],
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
    """ =ALL= job: retry failed sends from Notification Queue."""
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
            if item.attempts >= 3:
                continue
            try:
                frappe.db.set_value("Notification Queue", item.name, {"status": "Queued", "attempts": item.attempts + 1})
                frappe.db.commit()
                retried += 1
            except Exception:
                pass

        return {"retried": retried, "message": f"Re-queued {retried} failed items"}

    except Exception:
        frappe.log_error(title="Queue retry error", message=frappe.get_traceback())
        return {"retried": 0, "error": "Queue retry failed"}


def process_send(queue_item: str):
    """Background worker: process a single Notification Queue item."""
    try:
        doc = frappe.get_doc("Notification Queue", queue_item)
        doc.status = "Processing"
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        from kreativ_notification.notification.send import (
            send_document_via_whatsapp,
            send_image_via_whatsapp,
            send_text_via_whatsapp,
        )

        payload = frappe.parse_json(doc.payload) if doc.payload else {}

        result = {"success": False, "error": "Unknown action"}

        if doc.action_type == "send_pdf":
            result = send_document_via_whatsapp(
                payload.get("base64", ""),
                payload.get("filename", "document.pdf"),
                payload.get("caption", ""),
                chat_id_override=doc.recipient,
                source_doctype=doc.reference_doctype,
                source_docname=doc.reference_docname,
            )
        elif doc.action_type == "send_screenshot":
            result = send_image_via_whatsapp(
                payload.get("base64", ""),
                payload.get("filename", "screenshot.png"),
                payload.get("caption", ""),
                chat_id_override=doc.recipient,
                source_doctype=doc.reference_doctype,
                source_docname=doc.reference_docname,
            )
        elif doc.action_type == "send_test":
            result = send_text_via_whatsapp(
                payload.get("text", "Test message"),
                chat_id_override=doc.recipient,
                source_doctype=doc.reference_doctype,
                source_docname=doc.reference_docname,
            )
        elif doc.action_type == "send_manual":
            message_type = payload.get("message_type", "Custom")
            if message_type in ("Print PDF", "Dispatch PDF"):
                result = send_document_via_whatsapp(
                    payload.get("file_b64", ""),
                    payload.get("filename", "document"),
                    payload.get("caption", ""),
                    chat_id_override=doc.recipient,
                    source_doctype=doc.reference_doctype,
                    source_docname=doc.reference_docname,
                )
            elif message_type == "Screenshot":
                result = send_image_via_whatsapp(
                    payload.get("file_b64", ""),
                    payload.get("filename", "screenshot"),
                    payload.get("caption", ""),
                    chat_id_override=doc.recipient,
                    source_doctype=doc.reference_doctype,
                    source_docname=doc.reference_docname,
                )
            else:
                result = send_text_via_whatsapp(
                    payload.get("text", ""),
                    chat_id_override=doc.recipient,
                    source_doctype=doc.reference_doctype,
                    source_docname=doc.reference_docname,
                )

        if result.get("success"):
            doc.status = "Sent"
        else:
            doc.status = "Failed"
            doc.error_message = result.get("error", "Unknown error")

        doc.save(ignore_permissions=True)
        frappe.db.commit()

    except Exception:
        frappe.log_error(title=f"Queue process failed for {queue_item}", message=frappe.get_traceback())
        try:
            doc = frappe.get_doc("Notification Queue", queue_item)
            doc.status = "Failed"
            doc.error_message = frappe.get_traceback()[:500]
            doc.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            pass