"""Install hooks for kreativ_notification."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install():
    """Run after app installation."""
    # Create custom fields
    create_custom_fields(
        {
            "Employee Checkin": [
                {
                    "fieldname": "whatsapp_sent",
                    "label": "WhatsApp Sent",
                    "fieldtype": "Int",
                    "insert_after": "log_type",
                    "read_only": 1,
                    "no_copy": 1,
                    "default": 0,
                    "description": "0=not sent, 1=sent, 2=failed (retry), 3=invalid number (stop)",
                },
                {
                    "fieldname": "whatsapp_retry_count",
                    "label": "WhatsApp Retry Count",
                    "fieldtype": "Int",
                    "insert_after": "whatsapp_sent",
                    "read_only": 1,
                    "no_copy": 1,
                    "default": 0,
                    "description": "Number of times WhatsApp send has been attempted",
                },
            ]
        }
    )

    # Create WhatsApp User and WhatsApp Manager roles
    for role_name in ["WhatsApp User", "WhatsApp Manager"]:
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)

    # Create OpenWA Settings if not exists
    if not frappe.db.exists("OpenWA Settings"):
        frappe.get_doc({"doctype": "OpenWA Settings", "enabled": 0}).insert(ignore_permissions=True)

    frappe.db.commit()