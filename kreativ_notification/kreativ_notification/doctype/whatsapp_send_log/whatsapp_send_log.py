# -*- coding: utf-8 -*-
# Copyright (c) 2026, Kreativ Gravures and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document


class WhatsAppSendLog(Document):
    pass


@frappe.whitelist()
def create_log(source_doctype, source_docname, recipient, message_type="Print PDF", recipient_display="", source_print_format="", meta=None):
    """Create a WhatsApp Send Log entry."""
    doc = frappe.get_doc({
        "doctype": "WhatsApp Send Log",
        "source_doctype": source_doctype,
        "source_docname": source_docname,
        "recipient": recipient,
        "recipient_display": recipient_display,
        "message_type": message_type,
        "source_print_format": source_print_format,
        "status": "Queued",
        "meta": frappe.as_json(meta or {}),
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


@frappe.whitelist()
def update_log_status(log_name, success, error_message=""):
    """Update WhatsApp Send Log with result."""
    doc = frappe.get_doc("WhatsApp Send Log", log_name)
    doc.status = "Sent" if success else "Failed"
    doc.error_message = error_message
    doc.save(ignore_permissions=True)
    frappe.db.commit()