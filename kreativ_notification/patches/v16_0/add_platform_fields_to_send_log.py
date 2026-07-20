"""Patch: add dispatcher indexes to WhatsApp Send Log.

The platform fields and new statuses are defined in the doctype JSON
(synced automatically on migrate); this patch only adds the indexes the
dispatcher's hot queries rely on.
"""

import frappe

DT = "WhatsApp Send Log"


def execute():
    if not frappe.db.has_table(DT):
        return

    indexes = [
        ("idx_idempotency", "idempotency_key"),
        ("idx_status_retry", "status, retry_after"),
        ("idx_provider_msg", "provider_message_id"),
        ("idx_fallback", "fallback_fired, fallback_deadline"),
    ]
    for idx_name, columns in indexes:
        try:
            frappe.db.sql(
                f"ALTER TABLE `tab{DT}` ADD INDEX `{idx_name}` ({columns})")
        except Exception:
            frappe.log_error(title=f"Index creation skipped: {idx_name}",
                             message=frappe.get_traceback())
    frappe.db.commit()
