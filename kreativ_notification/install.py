"""Install hooks for kreativ_notification."""
import frappe
import secrets


def after_install():
    """Run after app installation."""
    # Create WhatsApp User and WhatsApp Manager roles
    for role_name in ["WhatsApp User", "WhatsApp Manager"]:
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)

    # OpenWA Settings: ensure singleton exists and generate webhook secret
    _setup_openwa_settings()

    # Run setup defaults to create default channel, templates, and rules
    try:
        from kreativ_notification.notification.setup_defaults import run
        run()
    except Exception:
        # Don't fail install if setup fails (e.g. during tests)
        frappe.log_error(title="Setup defaults failed", message=frappe.get_traceback())

    frappe.db.commit()


def _setup_openwa_settings():
    """Create OpenWA Settings singleton with generated webhook secret + defaults."""
    if not frappe.db.exists("DocType", "OpenWA Settings"):
        return

    settings = frappe.get_doc("OpenWA Settings")
    updated = False

    if not settings.webhook_secret:
        settings.webhook_secret = secrets.token_hex(32)
        updated = True
        print("Generated OpenWA Settings webhook secret")

    # Set inbound webhook + bot defaults
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