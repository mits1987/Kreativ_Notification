"""Patch: Migrate WhatsApp Send Log data."""
import frappe


def execute():
    """Ensure WhatsApp Send Log exists and has proper indexes."""
    if not frappe.db.has_table("WhatsApp Send Log"):
        return

    # Add indexes for common queries
    indexes = [
        ("idx_status_creation", "status, creation"),
        ("idx_source", "source_doctype, source_docname"),
        ("idx_recipient", "recipient"),
    ]

    for idx_name, columns in indexes:
        try:
            frappe.db.sql(f"ALTER TABLE `tabWhatsApp Send Log` ADD INDEX `{idx_name}` ({columns})")
        except Exception:
            pass

    frappe.db.commit()