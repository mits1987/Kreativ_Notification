"""Salary Slip WhatsApp notification hook."""
import frappe


def on_salary_slip_whatsapp(doc, method):
    """Triggered on Salary Slip submit."""
    if frappe.flags.in_test or frappe.flags.in_migrate:
        return

    settings = frappe.get_cached_doc("OpenWA Settings")
    if not (settings.enabled and settings.send_salary_slips and settings.base_url):
        return

    frappe.enqueue(
        "kreativ_notification.notification.employee_notifications.send_salary_slip",
        queue="long",
        timeout=300,
        salary_slip=doc.name,
        enqueue_after_commit=True,
    )