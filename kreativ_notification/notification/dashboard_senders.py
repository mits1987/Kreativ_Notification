"""Dashboard WhatsApp senders.

All workspace WhatsApp buttons now use the client-side screenshot flow:
JS captures the rendered DOM → calls api.send_screenshot_whatsapp() →
server screenshots + dispatches.

This module only retains send_custom_report() as a server-side fallback
for reports that have Frappe Report doctypes.
"""
import frappe
import base64

from kreativ_notification.notification.send import send_image_via_whatsapp
from kreativ_notification.notification.screenshot_utils import screenshot_html_playwright


def send_custom_report(report_name: str, filters: dict = None):
    """Server-side report screenshot → WhatsApp (fallback for Frappe Reports)."""
    report = frappe.get_doc("Report", report_name)
    report.execute(filters or {})

    html = frappe.render_template(
        "frappe/templates/includes/report/print.html",
        {"report": report, "filters": filters or {}},
    )
    png = screenshot_html_playwright(html)
    b64 = base64.b64encode(png).decode("utf-8")
    return send_image_via_whatsapp(b64, f"{report_name}.png", report_name)
