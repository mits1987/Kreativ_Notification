"""Doc event hooks for Employee Checkin."""
import frappe


def on_checkin_created(doc, method):
    """Triggered after Employee Checkin insert."""
    if frappe.flags.in_test or frappe.flags.in_migrate:
        return

    settings = frappe.get_cached_doc("OpenWA Settings")
    if not (settings.enabled and settings.base_url):
        return

    frappe.enqueue(
        "kreativ_notification.notification.employee_notifications.notify_checkin",
        queue="short",
        timeout=60,
        checkin_name=doc.name,
        enqueue_after_commit=True,
    )


def on_checkin_updated(doc, method):
    """Triggered on Employee Checkin update (e.g., log_type change)."""
    if frappe.flags.in_test or frappe.flags.in_migrate:
        return


def on_checkin_trashed(doc, method):
    """Triggered when Employee Checkin is deleted."""
    if frappe.flags.in_test or frappe.flags.in_migrate:
        return