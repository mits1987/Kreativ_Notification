"""Production defaults patch — applies all manual DB fixes from k316."""

import frappe


def execute():
    # 1. Disable duplicate Checkin Notification rule (only notify_checkin() fires)
    if frappe.db.exists("Notification Rule", "Checkin Notification"):
        frappe.db.set_value("Notification Rule", "Checkin Notification", "enabled", 0)

    # 2. Fix Custom Field label: "WhatsApp Sent" -> "WhatsApp Queued"
    cf_name = "Employee Checkin-whatsapp_sent"
    if frappe.db.exists("Custom Field", cf_name):
        cf = frappe.get_doc("Custom Field", cf_name)
        cf.label = "WhatsApp Queued"
        cf.save(ignore_permissions=True)

    # 3. Configure OpenWA Settings for inbound webhook + bot
    settings = frappe.get_doc("OpenWA Settings")
    settings.webhook_enabled = 1
    settings.auto_reply_enabled = 1
    settings.invoice_keywords = "invoice,inv,बिल"
    settings.ledger_keywords = "ledger,statement,account,balance,बही"
    settings.allowed_roles = "Sales Manager,Sales User,Marketing User"

    # webhook_secret should be set per-site (generate on install or via after_install)
    settings.save(ignore_permissions=True)

    # 4. Ensure Notification Channel points to correct session (k316 session ID)
    # This is site-specific — skip in patch, configure manually per site
    # channel = frappe.get_doc("Notification Channel", "Primary WhatsApp")
    # channel.session_id = "6153a8c9-8aa4-4920-bb55-8393277efb04"
    # channel.save()

    frappe.db.commit()