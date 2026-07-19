"""Patch: Migrate OpenWA Settings fields."""
import frappe


def execute():
    """Add new fields to OpenWA Settings if missing."""
    if frappe.db.has_table("OpenWA Settings"):
        meta = frappe.get_meta("OpenWA Settings")
        fields_to_add = [
            ("webhook_enabled", "Check", "Webhook Enabled", 1),
            ("webhook_secret", "Password", "Webhook Secret", None),
            ("auto_reply_enabled", "Check", "Auto Reply Enabled", 1),
            ("allowed_roles", "Table MultiSelect", "Allowed Roles", None, "Role"),
            ("invoice_keywords", "Data", "Invoice Keywords", "invoice,inv,बिल"),
            ("ledger_keywords", "Data", "Ledger Keywords", "ledger,statement,account,balance,बही"),
            ("inbound_webhook_url", "Data", "Inbound Webhook URL", None),
        ]

        for fieldname, fieldtype, label, default, *options in fields_to_add:
            if not meta.has_field(fieldname):
                frappe.custom.doctype.custom_field.custom_field.create_custom_field(
                    "OpenWA Settings",
                    {
                        "fieldname": fieldname,
                        "label": label,
                        "fieldtype": fieldtype,
                        "default": default,
                        "options": options[0] if options else None,
                        "insert_after": "send_salary_slips" if fieldname == "webhook_enabled" else None,
                    },
                )

        frappe.db.commit()