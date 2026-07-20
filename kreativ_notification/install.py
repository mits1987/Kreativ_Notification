"""Install hooks for kreativ_notification."""
import frappe


def after_install():
    """Run after app installation."""
    # Create WhatsApp User and WhatsApp Manager roles
    for role_name in ["WhatsApp User", "WhatsApp Manager"]:
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)

    # Create OpenWA Settings if not exists
    if not frappe.db.exists("DocType", "OpenWA Settings"):
        # The DocType will be created during migrate
        pass

    # Run setup defaults to create default channel, templates, and rules
    try:
        from kreativ_notification.notification.setup_defaults import run
        run()
    except Exception:
        # Don't fail install if setup fails (e.g. during tests)
        frappe.log_error(title="Setup defaults failed", message=frappe.get_traceback())

    frappe.db.commit()