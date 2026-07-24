"""Patch: Fix Employee Checkin whatsapp_sent label + OpenWA Settings defaults."""
import frappe


def execute():
    # Fix Employee Checkin custom field label
    cf_name = "Employee Checkin-whatsapp_sent"
    if frappe.db.exists("Custom Field", cf_name):
        cf = frappe.get_doc("Custom Field", cf_name)
        if cf.label == "WhatsApp Sent":
            cf.label = "WhatsApp Queued"
            cf.save(ignore_permissions=True)
            print(f"Fixed {cf_name} label -> WhatsApp Queued")

    # Ensure OpenWA Settings has webhook secret and correct defaults
    if frappe.db.exists("DocType", "OpenWA Settings"):
        settings = frappe.get_doc("OpenWA Settings")
        updated = False

        # Generate webhook secret if missing
        if not settings.webhook_secret:
            import secrets
            settings.webhook_secret = secrets.token_hex(32)
            updated = True
            print("Generated webhook secret")

        # Set defaults for inbound bot
        if not settings.webhook_enabled:
            settings.webhook_enabled = 1
            updated = True
        if not settings.auto_reply_enabled:
            settings.auto_reply_enabled = 1
            updated = True
        if not settings.invoice_keywords:
            settings.invoice_keywords = "invoice,inv,बिल"
            updated = True
        if not settings.ledger_keywords:
            settings.ledger_keywords = "ledger,statement,account,balance,बही"
            updated = True
        if not settings.allowed_roles:
            settings.allowed_roles = "Sales Manager,Sales User,Marketing User"
            updated = True

        if updated:
            settings.save(ignore_permissions=True)
            print("Updated OpenWA Settings defaults")