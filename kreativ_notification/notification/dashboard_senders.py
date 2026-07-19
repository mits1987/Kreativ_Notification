"""Dashboard WhatsApp senders — 7 custom HTML block dispatch buttons."""
import frappe
import base64
import json
from frappe.utils import get_url

from kreativ_notification.notification.send import (
    send_document_via_whatsapp,
    send_image_via_whatsapp,
)
from kreativ_notification.notification.screenshot_utils import screenshot_html_playwright


def _get_report_html(report_name: str, filters: dict = None) -> str:
    """Render a report to HTML."""
    report = frappe.get_doc("Report", report_name)
    if report.report_type == "Script Report":
        report.execute(filters or {})
    else:
        from frappe.utils import execute_report
        report.execute(filters or {})

    return frappe.render_template(
        "frappe/templates/includes/report/print.html",
        {"report": report, "filters": filters or {}},
    )


def _get_custom_html_block(block_name: str) -> str:
    """Get rendered HTML from a Custom HTML Block."""
    block = frappe.get_doc("Custom HTML Block", block_name)
    return block.get_rendered_html()


def send_dispatch_summary():
    """Dispatch Summary report → WhatsApp."""
    html = _get_custom_html_block("Dispatch Summary")
    png = screenshot_html_playwright(html)
    b64 = base64.b64encode(png).decode("utf-8")
    return send_image_via_whatsapp(b64, "dispatch_summary.png", "Dispatch Summary")


def send_dispatch_register():
    """Dispatch Register report → WhatsApp."""
    html = _get_custom_html_block("Dispatch Register")
    png = screenshot_html_playwright(html)
    b64 = base64.b64encode(png).decode("utf-8")
    return send_image_via_whatsapp(b64, "dispatch_register.png", "Dispatch Register")


def send_dispatch_detail():
    """Dispatch Detail report → WhatsApp."""
    html = _get_custom_html_block("Dispatch Detail")
    png = screenshot_html_playwright(html)
    b64 = base64.b64encode(png).decode("utf-8")
    return send_image_via_whatsapp(b64, "dispatch_detail.png", "Dispatch Detail")


def send_sales_invoice_register():
    """Sales Invoice Register report → WhatsApp."""
    html = _get_custom_html_block("Sales Invoice Register")
    png = screenshot_html_playwright(html)
    b64 = base64.b64encode(png).decode("utf-8")
    return send_image_via_whatsapp(b64, "sales_invoice_register.png", "Sales Invoice Register")


def send_sales_invoice_detail():
    """Sales Invoice Detail report → WhatsApp."""
    html = _get_custom_html_block("Sales Invoice Detail")
    png = screenshot_html_playwright(html)
    b64 = base64.b64encode(png).decode("utf-8")
    return send_image_via_whatsapp(b64, "sales_invoice_detail.png", "Sales Invoice Detail")


def send_party_statement():
    """Party Statement report → WhatsApp."""
    html = _get_custom_html_block("Party Statement")
    png = screenshot_html_playwright(html)
    b64 = base64.b64encode(png).decode("utf-8")
    return send_image_via_whatsapp(b64, "party_statement.png", "Party Statement")


def send_stock_report():
    """Stock Report → WhatsApp."""
    html = _get_custom_html_block("Stock Report")
    png = screenshot_html_playwright(html)
    b64 = base64.b64encode(png).decode("utf-8")
    return send_image_via_whatsapp(b64, "stock_report.png", "Stock Report")


def send_custom_report(report_name: str, filters: dict = None):
    """Generic report sender."""
    html = _get_report_html(report_name, filters)
    png = screenshot_html_playwright(html)
    b64 = base64.b64encode(png).decode("utf-8")
    return send_image_via_whatsapp(b64, f"{report_name}.png", report_name)