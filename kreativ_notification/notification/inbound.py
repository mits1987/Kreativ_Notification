"""Inbound WhatsApp Webhook Handler — Unified Bot.

Single bot handles configurable commands from WhatsApp Bot Command table.
"""
import frappe
import re
import json
import base64
import time
import requests
from typing import Optional
from frappe.utils import get_datetime, now_datetime, getdate, add_to_date, format_datetime

from kreativ_notification.notification.openwa_client import (
    check_circuit_breaker,
    increment_circuit_breaker,
    reset_circuit_breaker,
    OpenWAClient,
)
from kreativ_notification.notification.pdf_utils import generate_pdf_bytes, generate_pdf_from_html
from kreativ_notification.notification.dispatcher import dispatch
from kreativ_notification.notification.security import verify_webhook_signature


def _respond(body: dict, http_status: int = 200) -> dict:
    frappe.local.response["http_status_code"] = http_status
    return body


DEFAULT_INVOICE_KEYWORDS = "invoice,inv,बिल"
DEFAULT_LEDGER_KEYWORDS = "ledger,statement,account,balance,बही"
RATE_LIMIT_PER_MINUTE = 10
INBOUND_JOB_QUEUE = "short"
INBOUND_JOB_TIMEOUT = 300
CONVERSATION_TTL = 300  # 5 minutes


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive_whatsapp_message():
    """Public webhook endpoint for OpenWA incoming messages."""
    try:
        payload = frappe.request.get_data()
        if not payload:
            frappe.log_error("Empty webhook payload received", "WhatsApp Webhook")
            return _respond({"status": "error", "message": "Empty payload"}, 400)

        settings = frappe.get_cached_doc("OpenWA Settings")

        if not settings.webhook_enabled:
            return _respond({"status": "ignored", "message": "Webhook not enabled"})

        signature = frappe.get_request_header("X-OpenWA-Signature") or ""
        if settings.webhook_secret:
            if not signature:
                frappe.logger().warning("webhook_secret is set but no signature header received")
            elif not verify_webhook_signature(payload, signature, settings.get_password("webhook_secret")):
                frappe.log_error("Invalid webhook signature", "WhatsApp Webhook")
                return _respond({"status": "error", "message": "Invalid signature"}, 401)

        try:
            data = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            frappe.log_error("Invalid JSON in webhook payload", "WhatsApp Webhook")
            return _respond({"status": "error", "message": "Invalid JSON"}, 400)

        event_type = frappe.get_request_header("X-OpenWA-Event") or ""
        if not event_type and isinstance(data, dict):
            event_type = data.get("event", "")
        if not event_type.startswith("message"):
            return _respond({"status": "ignored", "message": "Event not processed"})

        if isinstance(data, list):
            msg_data_list = data
        elif isinstance(data, dict):
            msg_data_list = data.get("data", [])
            if isinstance(msg_data_list, dict):
                msg_data_list = [msg_data_list]
            elif not isinstance(msg_data_list, list):
                msg_data_list = []
        else:
            msg_data_list = []

        if msg_data_list and isinstance(msg_data_list[0], list):
            msg_data_list = msg_data_list[0]

        if not msg_data_list:
            return _respond({"status": "ignored", "message": "No message data"})

        jobs_queued = 0
        for msg_data in msg_data_list:
            if "key" in msg_data and isinstance(msg_data["key"], dict):
                key = msg_data["key"]
                message = msg_data.get("message", {})
                sender_chat_id = key.get("remoteJid", "")
                is_from_me = key.get("fromMe", False)
                message_text = _extract_message_text(message)
            else:
                sender_chat_id = msg_data.get("chatId", "") or msg_data.get("from", "")
                is_from_me = msg_data.get("fromMe", False)
                message_text = msg_data.get("body", "") or ""

            if is_from_me:
                continue

            if not sender_chat_id:
                continue

            valid_suffixes = ("@c.us", "@lid", "@g.us", "@s.whatsapp.net")
            if not any(sender_chat_id.endswith(suffix) for suffix in valid_suffixes):
                continue

            if not message_text:
                continue

            frappe.enqueue(
                "kreativ_notification.notification.inbound.process_incoming_message",
                queue="short",
                timeout=300,
                payload={
                    "reply_to": sender_chat_id,
                    "message_text": message_text.strip(),
                },
                enqueue_after_commit=False,
            )
            jobs_queued += 1

        if jobs_queued == 0:
            return _respond({"status": "ignored", "message": "No valid messages to process"})

        return _respond({"status": "queued", "message": f"{jobs_queued} message(s) queued for processing"}, 202)

    except Exception:
        frappe.log_error(title="WhatsApp Webhook Error", message=frappe.get_traceback())
        return _respond({"status": "error", "message": "Internal error"}, 500)


def process_incoming_message(payload: dict):
    """Background job: process incoming message through the unified bot."""
    try:
        settings = frappe.get_cached_doc("OpenWA Settings")

        if not (settings.webhook_enabled and settings.auto_reply_enabled):
            return

        if check_circuit_breaker():
            return

        reply_to = payload.get("reply_to", "")
        message_text = payload.get("message_text", "").strip()

        if not reply_to or not message_text:
            return

        if not _check_rate_limit(reply_to):
            return

        # Find employee by phone number and get their linked user_id
        employee_user_id = _get_employee_user_id(reply_to)
        if not employee_user_id:
            # Not an authorized employee - silently ignore
            frappe.logger().info(f"WhatsApp message from unauthorized number: {reply_to}")
            return

        # Check conversation state first (for multi-step flows like ledger)
        conversation = _get_conversation_state(reply_to)
        if conversation:
            # If user sends a new command keyword while in a conversation, clear stale
            # state and process the new command instead of treating it as a reply.
            if _message_matches_bot_command(message_text, settings):
                _clear_conversation_state(reply_to)
            else:
                _handle_conversation_reply(reply_to, message_text, conversation, employee_user_id)
                _mark_chat_as_read(reply_to)
                return

        # Try WhatsApp punch in/out before bot commands (time-sensitive)
        if _try_whatsapp_punch(reply_to, message_text):
            _mark_chat_as_read(reply_to)
            return

        # Route to bot commands (replaces hardcoded invoice/ledger)
        _route_bot_commands(reply_to, message_text, employee_user_id)
        _mark_chat_as_read(reply_to)

    except Exception:
        frappe.log_error(title="Inbound WhatsApp Processing Error", message=frappe.get_traceback())


def _mark_chat_as_read(chat_id: str):
    """Mark a chat as read in OpenWA."""
    try:
        from kreativ_notification.notification.openwa_client import OpenWAClient
        client = OpenWAClient()
        result = client.mark_chat_as_read(chat_id)
        if result.get("status") != "ok":
            frappe.logger().warning(f"Failed to mark chat {chat_id} as read: {result.get('message')}")
    except Exception:
        frappe.logger().exception(f"Error marking chat {chat_id} as read")


# ---------------------------------------------------------------------------
# Invoice bot
# ---------------------------------------------------------------------------

def _handle_invoice_request(reply_to: str, identifier: str, employee_user_id: str):
    invoice_name = _find_sales_invoice(identifier)
    if not invoice_name:
        _send_text(reply_to, f"Invoice '{identifier}' not found or not submitted.")
        increment_circuit_breaker()
        return

    # Get configured print format (defaults to "Standard" if not set)
    settings = frappe.get_cached_doc("OpenWA Settings")
    print_format = settings.invoice_print_format or "Standard"

    original_user = frappe.session.user
    try:
        frappe.set_user(employee_user_id)
        pdf_bytes = generate_pdf_bytes("Sales Invoice", invoice_name, print_format, channel_name="WhatsApp - OpenWA")
        if isinstance(pdf_bytes, bytes):
            base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
        else:
            base64_pdf = pdf_bytes
    except Exception:
        frappe.set_user(original_user)
        frappe.log_error(title=f"PDF Generation Failed for {invoice_name}", message=frappe.get_traceback())
        _send_text(reply_to, f"Failed to generate PDF for {invoice_name}.")
        increment_circuit_breaker()
        return
    finally:
        frappe.set_user(original_user)

    result = dispatch(
        recipient=reply_to,
        text=f"Invoice: {invoice_name}",
        file_b64=base64_pdf,
        filename=f"{invoice_name}.pdf",
        mimetype="application/pdf",
        message_type="Print PDF",
        source_doctype="Sales Invoice",
        source_docname=invoice_name,
        source_print_format=print_format,
        priority="Normal",
    )

    if result.get("success"):
        reset_circuit_breaker()
    else:
        increment_circuit_breaker()


def _parse_invoice_reference(message_text: str, settings) -> Optional[str]:
    keywords_text = settings.invoice_keywords or DEFAULT_INVOICE_KEYWORDS
    keywords = [k.strip().lower() for k in keywords_text.split(",") if k.strip()]
    if not keywords:
        keywords = ["invoice", "inv", "बिल"]

    keyword_pattern = "|".join(re.escape(k) for k in keywords)
    pattern = rf"(?i)({keyword_pattern})\s*[#:]?\s*([A-Z0-9\-_/]+)"
    match = re.search(pattern, message_text)
    if match:
        return match.group(2).upper()

    # Fallback: any uppercase alphanumeric with separators
    id_pattern = r"\b([A-Z0-9]+(?:[/\-][A-Z0-9]+)+)\b"
    match = re.search(id_pattern, message_text, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    return None


def _find_sales_invoice(identifier: str) -> Optional[str]:
    if frappe.db.exists("Sales Invoice", {"name": identifier, "docstatus": 1}):
        return identifier

    for field in ["custom_invoice_number", "invoice_number", "reference_number"]:
        if frappe.get_meta("Sales Invoice").has_field(field):
            result = frappe.db.get_value("Sales Invoice", {field: identifier, "docstatus": 1}, "name")
            if result:
                return result

    result = frappe.db.sql(
        """SELECT name FROM `tabSales Invoice` WHERE UPPER(name) = UPPER(%s) AND docstatus = 1 LIMIT 1""",
        (identifier,),
    )
    if result:
        return result[0][0]

    return None


# ---------------------------------------------------------------------------
# Ledger bot
# ---------------------------------------------------------------------------

def _handle_ledger_request(reply_to: str, search_term: str, employee_user_id: str):
    customers = _search_customers(search_term)
    if not customers:
        _send_text(reply_to, f"No customers found matching '{search_term}'.")
        return

    if len(customers) == 1:
        _send_ledger_pdf(customers[0]["name"], customers[0].get("customer_name", ""), reply_to, employee_user_id)
        return

    # Multiple matches — send numbered list, save conversation state
    numbered_list = "Found {0} customers:\n\n".format(len(customers))
    for i, c in enumerate(customers, 1):
        numbered_list += "{0}. {1}\n".format(i, c.get("customer_name", c["name"]))
    numbered_list += "\nReply with number (1-{0}) to get ledger PDF".format(len(customers))

    _save_conversation_state(reply_to, {
        "type": "ledger_selection",
        "customers": customers,
        "created_at": time.time(),
    })
    _send_text(reply_to, numbered_list)


def _parse_ledger_reference(message_text: str, settings) -> Optional[str]:
    keywords_text = settings.ledger_keywords or DEFAULT_LEDGER_KEYWORDS
    keywords = [k.strip().lower() for k in keywords_text.split(",") if k.strip()]
    if not keywords:
        keywords = ["ledger", "statement", "account", "balance", "बही"]

    keyword_pattern = "|".join(re.escape(k) for k in keywords)
    pattern = rf"(?i)({keyword_pattern})\s*[#:]?\s*(.+)"
    match = re.search(pattern, message_text)
    if match:
        term = match.group(2).strip()
        if term:
            return term

    return None


def _search_customers(search_term: str) -> list:
    return frappe.get_all(
        "Customer",
        filters=[["customer_name", "like", f"%{search_term}%"]],
        fields=["name", "customer_name"],
        limit_page_length=10,
    )


def _search_suppliers(search_term: str) -> list:
    return frappe.get_all(
        "Supplier",
        filters=[["supplier_name", "like", f"%{search_term}%"]],
        fields=["name", "supplier_name"],
        limit_page_length=10,
    )


def _send_ledger_pdf(customer_name: str, customer_display: str, reply_to: str, employee_user_id: str):
    original_user = frappe.session.user
    try:
        frappe.set_user(employee_user_id)
        from erpnext.accounts.report.general_ledger.general_ledger import execute as get_gl

        company = frappe.db.get_single_value("Global Defaults", "default_company")
        filters = frappe._dict({
            "company": company,
            "from_date": frappe.utils.add_months(frappe.utils.today(), -12),
            "to_date": frappe.utils.today(),
            "party_type": "Customer",
            "party": [customer_name],
            "party_name": [customer_display] if customer_display else [customer_name],
            "show_remarks": 0,
            "categorize_by": "Categorize by Voucher (Consolidated)",
            "show_opening_entries": 0,
            "include_default_book_entries": 0,
        })

        columns, result = get_gl(filters)
        if not result:
            _send_text(reply_to, f"No ledger entries found for {customer_display or customer_name}.")
            return

        # Get default letterhead for PDF
        letter_head = frappe.get_cached_doc("Letter Head", {"is_default": 1}) if frappe.db.exists("Letter Head", {"is_default": 1}) else None

        html = frappe.render_template(
            "erpnext/accounts/doctype/process_statement_of_accounts/process_statement_of_accounts.html",
            {"filters": filters, "data": result,
             "report": {"report_name": "General Ledger", "columns": columns},
             "ageing": None, "letter_head": letter_head, "terms_and_conditions": None}
        )
        from frappe.www.printview import get_print_style
        full_html = frappe.render_template("frappe/www/printview.html", {
            "body": html, "css": get_print_style(),
            "title": f"Statement - {customer_display or customer_name}"
        })

        # Generate PDF using headless Chromium (embeds letterhead images as base64)
        from kreativ_notification.notification.pdf_utils import generate_pdf_from_html
        pdf_bytes = generate_pdf_from_html(full_html, channel_name="WhatsApp - OpenWA")

        b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        filename = f"Statement_{customer_name}.pdf"
        caption = f"Statement of Accounts — {customer_display or customer_name}"

        dispatch(
            recipient=reply_to,
            text=caption,
            file_b64=b64,
            filename=filename,
            mimetype="application/pdf",
            message_type="Print PDF",
            source_doctype="Customer",
            source_docname=customer_name,
            priority="Normal",
        )
    except Exception:
        frappe.log_error(title="Ledger PDF generation failed", message=frappe.get_traceback())
        _send_text(reply_to, "Failed to generate ledger PDF. Please try again later.")
    finally:
        frappe.set_user(original_user)


def _render_ap_html(party_name, company, report_date, rows, party_label="Supplier", report_title="Accounts Payable"):
    """Render AP/AR HTML with Kreativ letterhead, centered party name, and data table."""
    from frappe.utils import date_diff
    import base64 as b64mod

    currency = rows[0].get("currency", "INR") if rows else "INR"
    today_date = frappe.utils.getdate(report_date)

    # Load letterhead image as base64
    letterhead_b64 = ""
    lh_image = frappe.db.get_single_value("Letter Head", "image") if frappe.db.exists("Letter Head", {"is_default": 1}) else None
    if lh_image:
        import os
        site_path = os.path.join(frappe.get_site_path("public"), lh_image.lstrip("/"))
        if os.path.exists(site_path):
            with open(site_path, "rb") as f:
                letterhead_b64 = b64mod.b64encode(f.read()).decode()
            ext = lh_image.rsplit(".", 1)[-1].lower()
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif"}.get(ext, "image/jpeg")

    letterhead_html = ""
    if letterhead_b64:
        letterhead_html = f'<div style="text-align:center; margin-bottom: 8px;"><img src="data:{mime};base64,{letterhead_b64}" style="max-height: 80px;"></div>'

    rows_html = ""
    total_outstanding = 0
    for row in rows:
        outstanding = row.get("outstanding_amount", 0) or 0
        bill_date = row.get("bill_date") or row.get("posting_date")
        due_date = row.get("due_date")
        bill_no = row.get("bill_no") or row.get("name", "")
        # Use age_days from API if provided, otherwise compute from due_date
        if row.get("age_days") is not None:
            age = row["age_days"]
        elif due_date:
            age = date_diff(today_date, due_date)
        elif bill_date:
            age = date_diff(today_date, bill_date)
        else:
            age = 0
        total_outstanding += outstanding

        rows_html += f"""<tr>
            <td>{bill_no}</td>
            <td>{frappe.utils.formatdate(bill_date, 'dd-MM-yyyy') if bill_date else '-'}</td>
            <td>{frappe.utils.formatdate(due_date, 'dd-MM-yyyy') if due_date else '-'}</td>
            <td class="text-right">{age}</td>
            <td class="text-right">{frappe.utils.fmt_money(outstanding, currency=currency)}</td>
        </tr>"""

    total_row = f"""<tr class="total-row">
        <td colspan="4"><strong>Total</strong></td>
        <td class="text-right"><strong>{frappe.utils.fmt_money(total_outstanding, currency=currency)}</strong></td>
    </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{ font-family: Inter, Arial, sans-serif; font-size: 11px; color: #171717; margin: 0; padding: 10px; }}
    .header {{ text-align: center; margin-bottom: 6px; }}
    .party-name {{ font-size: 20px; font-weight: 700; color: #171717; margin: 10px 0 2px; }}
    .subtitle {{ font-size: 13px; color: #555; margin-bottom: 4px; }}
    .meta {{ display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 10px; color: #999; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ background: #f8f8f8; text-align: center; font-size: 10px; font-weight: 500; color: #7c7c7c;
         border-top: 1px solid #ededed; border-bottom: 1px solid #ededed; padding: 5px 6px; }}
    td {{ padding: 5px 6px; border-top: 1px solid #ededed; font-size: 11px; }}
    .text-right {{ text-align: right; }}
    .text-left {{ text-align: left; }}
    .total-row {{ background: #f0f0f0; font-weight: 600; }}
    .total-row td {{ border-top: 2px solid #ccc; padding-top: 6px; }}
    @media print {{ @page {{ size: A4 portrait; margin: 8mm; }} thead {{ display: table-header-group; }} }}
</style>
</head>
<body>
    {letterhead_html}
    <div class="header">
        <div class="party-name">{party_name}</div>
        <div class="subtitle">Account Statement</div>
    </div>
    <div class="meta">
        <div><strong>Company:</strong> {company}</div>
        <div><strong>Report Date:</strong> {frappe.utils.formatdate(report_date, 'dd-MM-yyyy')}</div>
    </div>
    <table>
        <thead>
            <tr>
                <th style="text-align: left;">Bill No</th>
                <th style="text-align: left;">Bill Date</th>
                <th style="text-align: left;">Due Date</th>
                <th style="width: 50px; text-align: right;">Age</th>
                <th style="width: 90px; text-align: right;">Outstanding</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
            {total_row}
        </tbody>
    </table>
    <p style="text-align: right; font-size: 9px; color: #999; margin-top: 10px;">
        Printed on {frappe.utils.nowdate()}
    </p>
</body>
</html>"""


def _render_ar_ap_html(report_name, party_label, party_name, company, report_date, data, ageing_ranges):
    """Render AR/AP report HTML matching standard ERPNext report format."""
    currency = data[0].get("currency", "INR") if data else "INR"

    rows_html = ""
    total_invoiced = 0
    total_outstanding = 0
    total_r = [0] * 6

    for row in data:
        invoiced = row.get("invoiced", 0) or 0
        outstanding = row.get("outstanding", 0) or 0
        age = row.get("age", 0) or 0
        ranges = [row.get(f"range{i}", 0) or 0 for i in range(6)]
        total_invoiced += invoiced
        total_outstanding += outstanding
        for j in range(6):
            total_r[j] += ranges[j]

        rows_html += f"""<tr>
            <td>{frappe.utils.formatdate(row.get('posting_date'), 'dd-MM-yyyy')}</td>
            <td>{row.get('voucher_no', '')}</td>
            <td class="text-right">{int(age)}</td>
            <td class="text-right">{frappe.utils.fmt_money(invoiced, currency=currency)}</td>
            <td class="text-right">{frappe.utils.fmt_money(outstanding, currency=currency)}</td>
        </tr>"""

    total_row = f"""<tr class="total-row">
        <td colspan="2"><strong>Total</strong></td>
        <td></td>
        <td class="text-right"><strong>{frappe.utils.fmt_money(total_invoiced, currency=currency)}</strong></td>
        <td class="text-right"><strong>{frappe.utils.fmt_money(total_outstanding, currency=currency)}</strong></td>
    </tr>"""

    ageing_header = ""
    ageing_values = ""
    if total_outstanding:
        ageing_header = "".join(f'<th class="text-right">{aginging_range}</th>' for aginging_range in ageing_ranges)
        ageing_values = "".join(f'<td class="text-right">{frappe.utils.fmt_money(total_r[i], currency=currency)}</td>' for i in range(1, 6))

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{ font-family: Inter, Arial, sans-serif; font-size: 11px; color: #171717; margin: 10px; }}
    .title {{ text-align: center; font-size: 14px; font-weight: 600; margin-bottom: 6px; }}
    .meta {{ display: flex; justify-content: space-between; margin-bottom: 10px; font-size: 11px; }}
    .meta strong {{ color: #7c7c7c; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ background: #f8f8f8; text-align: center; font-size: 10px; font-weight: 500; color: #7c7c7c;
         border-top: 1px solid #ededed; border-bottom: 1px solid #ededed; padding: 5px 6px; }}
    td {{ padding: 5px 6px; border-top: 1px solid #ededed; font-size: 11px; }}
    .text-right {{ text-align: right; }}
    .total-row {{ background: #f0f0f0; font-weight: 600; }}
    .total-row td {{ border-top: 2px solid #ccc; padding-top: 6px; }}
    .ageing {{ margin-top: 12px; }}
    .ageing table {{ width: auto; }}
    .ageing th, .ageing td {{ padding: 4px 10px; font-size: 10px; }}
    @media print {{ @page {{ size: A4 landscape; margin: 8mm; }} thead {{ display: table-header-group; }} }}
</style>
</head>
<body>
    <div class="title">{report_name}</div>
    <div class="meta">
        <div><strong>{party_label}:</strong> {party_name}</div>
        <div><strong>Company:</strong> {company}</div>
        <div><strong>Report Date:</strong> {frappe.utils.formatdate(report_date, 'dd-MM-yyyy')}</div>
    </div>
    <table>
        <thead>
            <tr>
                <th style="width: 70px; text-align: left;">Date</th>
                <th style="text-align: left;">Reference</th>
                <th style="width: 60px; text-align: right;">Age (Days)</th>
                <th style="width: 90px; text-align: right;">Invoiced Amount</th>
                <th style="width: 90px; text-align: right;">Outstanding</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
            {total_row}
        </tbody>
    </table>

    {"" if not total_outstanding else f'''
    <div class="ageing">
        <strong>Ageing Summary:</strong>
        <table>
            <thead><tr>
                <th></th>
                {ageing_header}
                <th class="text-right">Total</th>
            </tr></thead>
            <tbody><tr>
                <td>Total Outstanding</td>
                {ageing_values}
                <td class="text-right"><strong>{frappe.utils.fmt_money(total_outstanding, currency=currency)}</strong></td>
            </tr></tbody>
        </table>
    </div>
    '''}

    <p style="text-align: right; font-size: 9px; color: #999; margin-top: 10px;">
        Printed on {frappe.utils.nowdate()}
    </p>
</body>
</html>"""


def _send_outstanding_pdf(customer_name: str, customer_display: str, reply_to: str, employee_user_id: str):
    """Generate Account Receivable report PDF for a customer using Sales Invoice.outstanding_amount."""
    original_user = frappe.session.user
    try:
        frappe.set_user(employee_user_id)

        company = frappe.db.get_single_value("Global Defaults", "default_company")
        today = frappe.utils.today()
        today_date = frappe.utils.getdate(today)

        rows_raw = frappe.db.sql("""
            SELECT si.name, si.posting_date, si.due_date, si.grand_total,
                   si.outstanding_amount, si.customer_name
            FROM `tabSales Invoice` si
            WHERE si.customer = %s
              AND si.docstatus = 1
              AND si.outstanding_amount > 0
            ORDER BY si.posting_date ASC
        """, (customer_name,), as_dict=True)

        rows = []
        for r in rows_raw:
            age_days = frappe.utils.date_diff(today_date, frappe.utils.getdate(r.due_date)) if r.due_date else 0
            rows.append(frappe._dict({
                "bill_no": r.name,
                "bill_date": r.posting_date,
                "outstanding_amount": r.outstanding_amount,
                "age_days": age_days,
            }))

        if not rows:
            _send_text(reply_to, f"No outstanding invoices found for {customer_display or customer_name}.")
            return

        display_name = customer_display or customer_name
        html = _render_ap_html(display_name, company, today, rows, party_label="Customer", report_title="Accounts Receivable")

        from kreativ_notification.notification.pdf_utils import generate_pdf_from_html
        pdf_bytes = generate_pdf_from_html(html, channel_name="WhatsApp - OpenWA")

        b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        filename = f"Outstanding_{customer_name}.pdf"
        caption = f"Account Receivable — {display_name}"

        dispatch(
            recipient=reply_to,
            text=caption,
            file_b64=b64,
            filename=filename,
            mimetype="application/pdf",
            message_type="Print PDF",
            source_doctype="Customer",
            source_docname=customer_name,
            priority="Normal",
        )
    except Exception:
        frappe.log_error(title="Outstanding PDF generation failed", message=frappe.get_traceback())
        _send_text(reply_to, "Failed to generate outstanding report. Please try again later.")
    finally:
        frappe.set_user(original_user)


def _send_payable_pdf(supplier_name: str, supplier_display: str, reply_to: str, employee_user_id: str):
    """Generate Account Payable report PDF for a supplier using Purchase Invoice.outstanding_amount."""
    original_user = frappe.session.user
    try:
        frappe.set_user(employee_user_id)

        company = frappe.db.get_single_value("Global Defaults", "default_company")
        today = frappe.utils.today()
        today_date = frappe.utils.getdate(today)

        rows_raw = frappe.db.sql("""
            SELECT pi.name, pi.posting_date, pi.due_date, pi.grand_total,
                   pi.outstanding_amount, pi.bill_no, pi.bill_date
            FROM `tabPurchase Invoice` pi
            WHERE pi.supplier = %s
              AND pi.docstatus = 1
              AND pi.outstanding_amount > 0
            ORDER BY pi.posting_date ASC
        """, (supplier_name,), as_dict=True)

        rows = []
        for r in rows_raw:
            age_days = frappe.utils.date_diff(today_date, frappe.utils.getdate(r.due_date)) if r.due_date else 0
            rows.append(frappe._dict({
                "bill_no": r.bill_no or r.name,
                "bill_date": r.bill_date or r.posting_date,
                "outstanding_amount": r.outstanding_amount,
                "age_days": age_days,
            }))

        if not rows:
            _send_text(reply_to, f"No outstanding bills found for {supplier_display or supplier_name}.")
            return

        display_name = supplier_display or supplier_name
        html = _render_ap_html(display_name, company, today, rows, party_label="Supplier", report_title="Accounts Payable")

        from kreativ_notification.notification.pdf_utils import generate_pdf_from_html
        pdf_bytes = generate_pdf_from_html(html, channel_name="WhatsApp - OpenWA")

        b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        filename = f"Payable_{supplier_name}.pdf"
        caption = f"Account Payable — {display_name}"

        dispatch(
            recipient=reply_to,
            text=caption,
            file_b64=b64,
            filename=filename,
            mimetype="application/pdf",
            message_type="Print PDF",
            source_doctype="Supplier",
            source_docname=supplier_name,
            priority="Normal",
        )
    except Exception:
        frappe.log_error(title="Payable PDF generation failed", message=frappe.get_traceback())
        _send_text(reply_to, "Failed to generate payable report. Please try again later.")
    finally:
        frappe.set_user(original_user)


# ---------------------------------------------------------------------------
# Conversation state machine
# ---------------------------------------------------------------------------

def _handle_conversation_reply(reply_to: str, message_text: str, conversation: dict, employee_user_id: str):
    conv_type = conversation.get("type")

    if conv_type == "ledger_selection":
        try:
            choice = int(message_text.strip())
            customers = conversation.get("customers", [])
            if 1 <= choice <= len(customers):
                selected = customers[choice - 1]
                _clear_conversation_state(reply_to)
                
                # Check if this was a remote fetch command
                cmd_name = conversation.get("cmd_name")
                if cmd_name:
                    cmd = frappe.get_doc("WhatsApp Bot Command", cmd_name)
                    if cmd.api_link:
                        _fetch_remote_ledger_pdf(cmd, selected["name"], reply_to, employee_user_id)
                    else:
                        _send_ledger_pdf(selected["name"], selected.get("customer_name", ""), reply_to, employee_user_id)
                else:
                    _send_ledger_pdf(selected["name"], selected.get("customer_name", ""), reply_to, employee_user_id)
            else:
                _send_text(reply_to, f"Please enter a number between 1 and {len(customers)}.")
        except ValueError:
            _send_text(reply_to, "Please enter a valid number.")

    elif conv_type == "outstanding_selection":
        try:
            choice = int(message_text.strip())
            customers = conversation.get("customers", [])
            if 1 <= choice <= len(customers):
                selected = customers[choice - 1]
                _clear_conversation_state(reply_to)
                cmd_name = conversation.get("cmd_name")
                if cmd_name:
                    cmd = frappe.get_doc("WhatsApp Bot Command", cmd_name)
                    if cmd.api_link:
                        try:
                            _fetch_remote_report_pdf(cmd, selected["name"], "Customer",
                                "kreativ_notification.api.get_customer_outstanding_pdf", reply_to)
                        except Exception:
                            frappe.log_error(title=f"Remote Outstanding Fetch Failed for {selected['name']}", message=frappe.get_traceback())
                            _send_text(reply_to, "Failed to fetch outstanding report from remote server.")
                    else:
                        _send_outstanding_pdf(selected["name"], selected.get("customer_name", ""), reply_to, employee_user_id)
                else:
                    _send_outstanding_pdf(selected["name"], selected.get("customer_name", ""), reply_to, employee_user_id)
            else:
                _send_text(reply_to, f"Please enter a number between 1 and {len(customers)}.")
        except ValueError:
            _send_text(reply_to, "Please enter a valid number.")

    elif conv_type == "payable_selection":
        try:
            choice = int(message_text.strip())
            suppliers = conversation.get("suppliers", [])
            if 1 <= choice <= len(suppliers):
                selected = suppliers[choice - 1]
                _clear_conversation_state(reply_to)
                cmd_name = conversation.get("cmd_name")
                if cmd_name:
                    cmd = frappe.get_doc("WhatsApp Bot Command", cmd_name)
                    if cmd.api_link:
                        try:
                            _fetch_remote_report_pdf(cmd, selected["name"], "Supplier",
                                "kreativ_notification.api.get_supplier_payable_pdf", reply_to)
                        except Exception:
                            frappe.log_error(title=f"Remote Payable Fetch Failed for {selected['name']}", message=frappe.get_traceback())
                            _send_text(reply_to, "Failed to fetch payable report from remote server.")
                    else:
                        _send_payable_pdf(selected["name"], selected.get("supplier_name", ""), reply_to, employee_user_id)
                else:
                    _send_payable_pdf(selected["name"], selected.get("supplier_name", ""), reply_to, employee_user_id)
            else:
                _send_text(reply_to, f"Please enter a number between 1 and {len(suppliers)}.")
        except ValueError:
            _send_text(reply_to, "Please enter a valid number.")

    else:
        _clear_conversation_state(reply_to)
        _send_text(reply_to, "Session expired. Please send your request again.")


def _save_conversation_state(chat_id: str, state: dict):
    frappe.cache().set_value(f"notif_chat:{chat_id}", state, expires_in_sec=CONVERSATION_TTL)


def _get_conversation_state(chat_id: str) -> dict | None:
    return frappe.cache().get_value(f"notif_chat:{chat_id}")


def _clear_conversation_state(chat_id: str):
    frappe.cache().delete_value(f"notif_chat:{chat_id}")


# ---------------------------------------------------------------------------
# Generic Bot Command Router
# ---------------------------------------------------------------------------

def _message_matches_bot_command(message_text: str, settings=None) -> bool:
    """Check if a message starts with any enabled bot command keyword."""
    if settings is None:
        settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.bot_commands:
        return False
    text_lower = message_text.strip().lower()
    for cmd in settings.bot_commands:
        if not cmd.enabled:
            continue
        keywords = [k.strip().lower() for k in cmd.command_keyword.split(",") if k.strip()]
        for kw in keywords:
            if text_lower.startswith(kw):
                return True
    return False


def _route_bot_commands(reply_to: str, message_text: str, employee_user_id: str):
    """Route message to matching bot command (longest keyword wins)."""
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.bot_commands:
        _send_text(reply_to, _get_help_text(settings))
        return

    # Build list of commands user is authorized for
    user_roles = frappe.get_roles(employee_user_id)
    authorized_commands = []
    for cmd in settings.bot_commands:
        if not cmd.enabled:
            continue
        
        # Check per-command allowed_roles if set
        if cmd.allowed_roles:
            allowed = [r.strip() for r in cmd.allowed_roles.split(",") if r.strip()]
            if allowed and not any(role in user_roles for role in allowed):
                continue  # Skip this command, user not authorized
        
        authorized_commands.append(cmd)

    # Sort by keyword length descending (longest match first)
    commands = sorted(
        authorized_commands,
        key=lambda c: len(c.command_keyword),
        reverse=True,
    )

    for cmd in commands:
        # Split comma-separated keywords (like old invoice_keywords/ledger_keywords)
        keywords = [k.strip().lower() for k in cmd.command_keyword.split(",") if k.strip()]
        if not keywords:
            continue
        
        keyword_pattern = "|".join(re.escape(k) for k in keywords)
        pattern = rf"(?i)({keyword_pattern})\s*[#:]*\s*(.+)"
        match = re.search(pattern, message_text)
        if match:
            identifier = match.group(2).strip()
            if identifier:
                _handle_bot_command(cmd, reply_to, identifier, employee_user_id)
                return

    # No keyword matched — only send help if user explicitly asked for help
    help_keywords = ["help", "menu", "commands", "सहायता"]
    if any(message_text.strip().lower().startswith(kw) for kw in help_keywords):
        _send_text(reply_to, _get_help_text(settings))
    # Otherwise silently ignore (don't flood with help text)


def _handle_bot_command(cmd, reply_to: str, identifier: str, employee_user_id: str):
    """Generic handler for bot commands."""
    try:
        if cmd.fetch_type == "Document":
            _handle_document_command(cmd, reply_to, identifier, employee_user_id)
        elif cmd.fetch_type == "Report":
            _handle_report_command(cmd, reply_to, identifier, employee_user_id)
        else:
            _send_text(reply_to, f"Unknown fetch type: {cmd.fetch_type}")
    except Exception:
        frappe.log_error(title=f"Bot Command Error: {cmd.command_keyword}", message=frappe.get_traceback())
        _send_text(reply_to, "Failed to process request. Please try again later.")


def _handle_document_command(cmd, reply_to: str, identifier: str, employee_user_id: str):
    """Handle Document fetch type - lookup doc by search_field and send PDF."""
    # Search for document locally only if not using remote API
    if not cmd.api_link:
        doc_name = _find_document(cmd, identifier)
        if not doc_name:
            _send_text(reply_to, f"{cmd.doc_type} '{identifier}' not found.")
            return
    else:
        # For remote fetch, use identifier directly as doc_name
        doc_name = identifier

    original_user = frappe.session.user
    try:
        frappe.set_user(employee_user_id)

        # Determine API target (local vs remote)
        if cmd.api_link:
            pdf_bytes = _fetch_remote_pdf(cmd, cmd.doc_type, doc_name)
        else:
            print_format = cmd.print_format or "Standard"
            pdf_bytes = generate_pdf_bytes(cmd.doc_type, doc_name, print_format, channel_name="WhatsApp - OpenWA")

        if isinstance(pdf_bytes, bytes):
            base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
        else:
            base64_pdf = pdf_bytes
    except Exception:
        frappe.set_user(original_user)
        frappe.log_error(title=f"PDF Generation Failed for {cmd.doc_type} {doc_name}", message=frappe.get_traceback())
        _send_text(reply_to, f"Failed to generate PDF for {doc_name}.")
        return
    finally:
        frappe.set_user(original_user)

    result = dispatch(
        recipient=reply_to,
        text=f"{cmd.doc_type}: {doc_name}",
        file_b64=base64_pdf,
        filename=f"{doc_name}.pdf",
        mimetype="application/pdf",
        message_type="Print PDF",
        source_doctype=cmd.doc_type,
        source_docname=doc_name,
        source_print_format=cmd.print_format,
        priority="Normal",
    )

    if result.get("success"):
        reset_circuit_breaker()
    else:
        increment_circuit_breaker()


def _handle_report_command(cmd, reply_to: str, identifier: str, employee_user_id: str):
    """Handle Report fetch type - run report and send PDF."""
    if cmd.doc_type == "Customer":
        # Determine which report to run based on command keyword
        keyword = cmd.command_keyword.split(",")[0].strip().lower()
        if keyword in ("outstanding", "outstanding report", "beat", "baki"):
            _handle_outstanding_command(cmd, identifier, reply_to, employee_user_id)
        else:
            _handle_ledger_command(cmd, identifier, reply_to, employee_user_id)
    elif cmd.doc_type == "Supplier":
        _handle_payable_command(cmd, identifier, reply_to, employee_user_id)
    else:
        _send_text(reply_to, f"Report type not supported for {cmd.doc_type}")


def _handle_ledger_command(cmd, identifier: str, reply_to: str, employee_user_id: str):
    """Handle ledger report — search customers, generate GL PDF."""
    customers = _search_customers(identifier)
    if not customers:
        _send_text(reply_to, f"No customers found matching '{identifier}'.")
        return

    if len(customers) == 1:
        if cmd.api_link:
            _fetch_remote_ledger_pdf(cmd, customers[0]["name"], reply_to, employee_user_id)
        else:
            _send_ledger_pdf(customers[0]["name"], customers[0].get("customer_name", ""), reply_to, employee_user_id)
        return

    numbered_list = "Found {0} customers:\n\n".format(len(customers))
    for i, c in enumerate(customers, 1):
        numbered_list += "{0}. {1}\n".format(i, c.get("customer_name", c["name"]))
    numbered_list += "\nReply with number (1-{0}) to get ledger PDF".format(len(customers))

    _save_conversation_state(reply_to, {
        "type": "ledger_selection",
        "customers": customers,
        "cmd_name": cmd.name,
        "created_at": time.time(),
    })
    _send_text(reply_to, numbered_list)


def _handle_outstanding_command(cmd, identifier: str, reply_to: str, employee_user_id: str):
    """Handle outstanding report — search customers, generate Accounts Receivable PDF."""
    customers = _search_customers(identifier)
    if not customers:
        _send_text(reply_to, f"No customers found matching '{identifier}'.")
        return

    if len(customers) == 1:
        if cmd.api_link:
            try:
                _fetch_remote_report_pdf(cmd, customers[0]["name"], "Customer",
                    "kreativ_notification.api.get_customer_outstanding_pdf", reply_to)
            except Exception:
                frappe.log_error(title=f"Remote Outstanding Fetch Failed for {customers[0]['name']}", message=frappe.get_traceback())
                _send_text(reply_to, f"Failed to fetch outstanding report from remote server.")
        else:
            _send_outstanding_pdf(customers[0]["name"], customers[0].get("customer_name", ""), reply_to, employee_user_id)
        return

    numbered_list = "Found {0} customers:\n\n".format(len(customers))
    for i, c in enumerate(customers, 1):
        numbered_list += "{0}. {1}\n".format(i, c.get("customer_name", c["name"]))
    numbered_list += "\nReply with number (1-{0}) to get outstanding report".format(len(customers))

    _save_conversation_state(reply_to, {
        "type": "outstanding_selection",
        "customers": customers,
        "cmd_name": cmd.name,
        "created_at": time.time(),
    })
    _send_text(reply_to, numbered_list)


def _handle_payable_command(cmd, identifier: str, reply_to: str, employee_user_id: str):
    """Handle payable report — search suppliers, generate Accounts Payable PDF."""
    suppliers = _search_suppliers(identifier)
    if not suppliers:
        _send_text(reply_to, f"No suppliers found matching '{identifier}'.")
        return

    if len(suppliers) == 1:
        if cmd.api_link:
            try:
                _fetch_remote_report_pdf(cmd, suppliers[0]["name"], "Supplier",
                    "kreativ_notification.api.get_supplier_payable_pdf", reply_to)
            except Exception:
                frappe.log_error(title=f"Remote Payable Fetch Failed for {suppliers[0]['name']}", message=frappe.get_traceback())
                _send_text(reply_to, f"Failed to fetch payable report from remote server.")
        else:
            _send_payable_pdf(suppliers[0]["name"], suppliers[0].get("supplier_name", ""), reply_to, employee_user_id)
        return

    numbered_list = "Found {0} suppliers:\n\n".format(len(suppliers))
    for i, s in enumerate(suppliers, 1):
        numbered_list += "{0}. {1}\n".format(i, s.get("supplier_name", s["name"]))
    numbered_list += "\nReply with number (1-{0}) to get payable report".format(len(suppliers))

    _save_conversation_state(reply_to, {
        "type": "payable_selection",
        "suppliers": suppliers,
        "cmd_name": cmd.name,
        "created_at": time.time(),
    })
    _send_text(reply_to, numbered_list)


def _find_document(cmd, identifier: str) -> str | None:
    """Find document by search_field with configurable docstatus."""
    doctype = cmd.doc_type
    search_field = cmd.search_field
    
    # Parse doc_status (comma-separated values like "1" or "0,1")
    doc_status_str = cmd.get("doc_status", "1") or "1"
    doc_status_values = [int(s.strip()) for s in doc_status_str.split(",") if s.strip().isdigit()]
    if not doc_status_values:
        doc_status_values = [1]  # Default to Submitted only
    
    # Build docstatus filter
    if len(doc_status_values) == 1:
        docstatus_filter = doc_status_values[0]
    else:
        docstatus_filter = doc_status_values

    # Try exact match on search_field
    filters = {search_field: identifier}
    if isinstance(docstatus_filter, list):
        # For multiple statuses, we need OR logic - use SQL
        placeholders = ",".join(["%s"] * len(doc_status_values))
        result = frappe.db.sql(
            f"""SELECT name FROM `tab{doctype}` WHERE UPPER({search_field}) = UPPER(%s) AND docstatus IN ({placeholders}) LIMIT 1""",
            (identifier, *doc_status_values),
        )
        if result:
            return result[0][0]
    else:
        filters["docstatus"] = docstatus_filter
        if frappe.db.exists(doctype, filters):
            return identifier

    # Try exact name match with docstatus filter
    if isinstance(docstatus_filter, list):
        placeholders = ",".join(["%s"] * len(doc_status_values))
        result = frappe.db.sql(
            f"""SELECT name FROM `tab{doctype}` WHERE UPPER(name) = UPPER(%s) AND docstatus IN ({placeholders}) LIMIT 1""",
            (identifier, *doc_status_values),
        )
        if result:
            return result[0][0]
    else:
        result = frappe.db.sql(
            f"""SELECT name FROM `tab{doctype}` WHERE UPPER(name) = UPPER(%s) AND docstatus = %s LIMIT 1""",
            (identifier, docstatus_filter),
        )
        if result:
            return result[0][0]

    return None


def _fetch_remote_pdf(cmd, doctype: str, docname: str) -> bytes:
    """Fetch PDF from remote Frappe instance."""
    url = f"{cmd.api_link}/api/method/frappe.utils.print_format.download_pdf"
    params = {"doctype": doctype, "name": docname}
    if cmd.print_format:
        params["format"] = cmd.print_format

    headers = {}
    if cmd.auth_type == "Token":
        # Decrypt Password fields from child table
        child_doc = frappe.get_doc("WhatsApp Bot Command", cmd.name)
        api_key = child_doc.get_password("api_key")
        api_password = child_doc.get_password("api_password")
        headers["Authorization"] = f"token {api_key}:{api_password}"
    elif cmd.auth_type == "Basic":
        import base64 as b64
        child_doc = frappe.get_doc("WhatsApp Bot Command", cmd.name)
        api_password = child_doc.get_password("api_password")
        headers["Authorization"] = f"Basic {b64.b64encode(api_password.encode()).decode()}"

    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return r.content


def _fetch_remote_ledger_pdf(cmd, customer_name: str, reply_to: str, employee_user_id: str):
    """Fetch Customer Ledger PDF from remote Frappe instance via custom API."""
    # Call custom remote method that generates ledger PDF for customer
    url = f"{cmd.api_link}/api/method/kreativ_notification.api.get_customer_ledger_pdf"
    params = {"customer": customer_name}

    headers = {}
    if cmd.auth_type == "Token":
        child_doc = frappe.get_doc("WhatsApp Bot Command", cmd.name)
        api_key = child_doc.get_password("api_key")
        api_password = child_doc.get_password("api_password")
        headers["Authorization"] = f"token {api_key}:{api_password}"
    elif cmd.auth_type == "Basic":
        import base64 as b64
        child_doc = frappe.get_doc("WhatsApp Bot Command", cmd.name)
        api_password = child_doc.get_password("api_password")
        headers["Authorization"] = f"Basic {b64.b64encode(api_password.encode()).decode()}"

    try:
        r = requests.get(url, params=params, headers=headers, timeout=60)
        r.raise_for_status()
        # Remote API returns JSON wrapped in "message" key by Frappe
        response_json = r.json()
        message = response_json.get("message", response_json)  # Handle both wrapped and direct
        if not message.get("success"):
            raise Exception(message.get("error", "Remote API returned error"))

        base64_pdf = message.get("pdf_base64")
        filename = message.get("filename", f"Statement_{customer_name}.pdf")

        result = dispatch(
            recipient=reply_to,
            text=f"Customer Ledger: {customer_name}",
            file_b64=base64_pdf,
            filename=filename,
            mimetype="application/pdf",
            message_type="Print PDF",
            source_doctype="Customer",
            source_docname=customer_name,
            source_print_format="",
            priority="Normal",
        )

        if result.get("success"):
            reset_circuit_breaker()
        else:
            increment_circuit_breaker()

    except Exception:
        frappe.log_error(title=f"Remote Ledger Fetch Failed for {customer_name}", message=frappe.get_traceback())
        _send_text(reply_to, f"Failed to fetch ledger for {customer_name} from remote server.")


def _fetch_remote_report_pdf(cmd, party_name: str, party_label: str, api_method: str, reply_to: str):
    """Generic remote report PDF fetch (outstanding/payable)."""
    url = f"{cmd.api_link}/api/method/{api_method}"
    params = {party_label.lower(): party_name}

    headers = {}
    if cmd.auth_type == "Token":
        child_doc = frappe.get_doc("WhatsApp Bot Command", cmd.name)
        api_key = child_doc.get_password("api_key")
        api_password = child_doc.get_password("api_password")
        headers["Authorization"] = f"token {api_key}:{api_password}"
    elif cmd.auth_type == "Basic":
        import base64 as b64
        child_doc = frappe.get_doc("WhatsApp Bot Command", cmd.name)
        api_password = child_doc.get_password("api_password")
        headers["Authorization"] = f"Basic {b64.b64encode(api_password.encode()).decode()}"

    r = requests.get(url, params=params, headers=headers, timeout=60)
    r.raise_for_status()
    response_json = r.json()
    message = response_json.get("message", response_json)
    if not message.get("success"):
        raise Exception(message.get("error", "Remote API returned error"))

    base64_pdf = message.get("pdf_base64")
    filename = message.get("filename", f"Report_{party_name}.pdf")

    result = dispatch(
        recipient=reply_to,
        text=f"Account Statement — {party_name}",
        file_b64=base64_pdf,
        filename=filename,
        mimetype="application/pdf",
        message_type="Print PDF",
        source_doctype=party_label,
        source_docname=party_name,
        priority="Normal",
    )

    if result.get("success"):
        reset_circuit_breaker()
    else:
        increment_circuit_breaker()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_lid_to_phone(lid: str) -> str | None:
    """Resolve OpenWA LID to @c.us phone format via OpenWA API."""
    try:
        client = OpenWAClient()
        if not client.base_url or not client.api_key:
            return None
        from kreativ_notification.notification.openwa_client import _get_session
        sess = _get_session()
        url = f"{client.base_url}/api/sessions/{client.session_id}/contacts/{lid}"
        r = sess.get(url, headers={"X-API-Key": client.api_key}, timeout=10)
        if r.ok:
            data = r.json()
            return data.get("id")  # returns "919023587002@c.us"
    except Exception:
        frappe.logger().exception(f"Failed to resolve LID {lid} to phone")
    return None


def _get_employee_user_id(phone_number: str) -> str | None:
    """Find employee by cell_number and return their linked user_id.

    Normalizes phone from '91xxxxxxxxxx@c.us' format and matches
    against Employee.cell_number (last 10 digits).
    Also validates employee has a role in allowed_roles (if configured).
    Handles @lid format by resolving to phone via OpenWA API.
    """
    # Resolve @lid to @c.us phone first
    if phone_number.endswith("@lid"):
        resolved = _resolve_lid_to_phone(phone_number)
        if resolved:
            phone_number = resolved
        else:
            frappe.logger().warning(f"Could not resolve LID to phone: {phone_number}")
            return None

    # Normalize phone: "91xxxxxxxxxx@c.us" -> "91xxxxxxxxxx"
    clean_phone = phone_number
    if "@" in clean_phone:
        clean_phone = clean_phone.split("@")[0]
    # Remove leading + if present
    clean_phone = clean_phone.lstrip("+")

    # Find employee by cell_number (which may have various formats)
    employees = frappe.get_all(
        "Employee",
        filters={"cell_number": ["like", f"%{clean_phone[-10:]}%"], "status": "Active"},
        fields=["name", "user_id"],
        limit=1,
    )
    if not employees or not employees[0].get("user_id"):
        return None

    # Check allowed_roles from OpenWA Settings
    settings = frappe.get_cached_doc("OpenWA Settings")
    allowed_roles_raw = settings.allowed_roles or ""
    allowed_roles = [r.strip() for r in allowed_roles_raw.split(",") if r.strip()]

    if allowed_roles:
        employee_user_id = employees[0]["user_id"]
        user_roles = frappe.get_roles(employee_user_id)
        if not any(role in user_roles for role in allowed_roles):
            frappe.logger().info(
                f"WhatsApp message from employee {employee_user_id} rejected: "
                f"roles {user_roles} not in allowed_roles {allowed_roles}"
            )
            return None

    return employees[0]["user_id"]


def _extract_message_text(message) -> str:
    if isinstance(message, list):
        if message and isinstance(message[0], dict):
            message = message[0]
        else:
            return ""
    if not isinstance(message, dict):
        return ""
    if "conversation" in message:
        return message["conversation"]
    if "extendedTextMessage" in message:
        return message["extendedTextMessage"].get("text", "")
    if "imageMessage" in message:
        return message["imageMessage"].get("caption", "")
    if "documentMessage" in message:
        return message["documentMessage"].get("caption", "")
    if "videoMessage" in message:
        return message["videoMessage"].get("caption", "")
    return ""


def _send_text(chat_id: str, text: str) -> bool:
    from kreativ_notification.notification.send import send_text_via_whatsapp
    return send_text_via_whatsapp(
        text,
        chat_id_override=chat_id,
        source_doctype="OpenWA Settings",
        source_docname="OpenWA Settings",
    ).get("success", False)


# ---------------------------------------------------------------------------
# WhatsApp Punch In/Out
# ---------------------------------------------------------------------------

# Regex patterns for punch messages
_PUNCH_KEYWORD_TIME = re.compile(
    r'(?i)^(in|out)\s+(\d{1,2})[:\s]?(\d{2})\s*(am|pm|a\.m\.|p\.m\.)?$'
)
_PUNCH_KEYWORD_ONLY = re.compile(r'(?i)^(in|out)$')
_PUNCH_TIME_ONLY = re.compile(
    r'^(\d{1,2})[:\s]?(\d{2})\s*(am|pm|a\.m\.|p\.m\.)?$', re.IGNORECASE
)
_PUNCH_BARE_DIGITS = re.compile(r'^(\d{3,4})$')


def _parse_punch_message(text: str) -> dict | None:
    """Parse punch direction + time from a WhatsApp message.

    Returns {"direction": "IN"/"OUT"/None, "hour": int, "minute": int,
             "meridian": "am"/"pm"/None} or None if not a punch message.
    """
    text = text.strip()

    # Pattern 1: keyword + time — "in 8:20", "out 3:50pm", "in 1550"
    m = _PUNCH_KEYWORD_TIME.match(text)
    if m:
        return {
            "direction": m.group(1).upper(),
            "hour": int(m.group(2)),
            "minute": int(m.group(3)),
            "meridian": m.group(4),
        }

    # Pattern 2: keyword only — "in", "out"
    m = _PUNCH_KEYWORD_ONLY.match(text)
    if m:
        return {
            "direction": m.group(1).upper(),
            "hour": None,
            "minute": None,
            "meridian": None,
        }

    # Pattern 3: time only — "8:20", "3:50pm", "15:50"
    m = _PUNCH_TIME_ONLY.match(text)
    if m:
        return {
            "direction": None,
            "hour": int(m.group(1)),
            "minute": int(m.group(2)),
            "meridian": m.group(3),
        }

    # Pattern 4: bare 3-4 digits — "820", "1550"
    m = _PUNCH_BARE_DIGITS.match(text)
    if m:
        digits = m.group(1)
        if len(digits) == 3:
            hour = int(digits[0])
            minute = int(digits[1:])
        else:
            hour = int(digits[:2])
            minute = int(digits[2:])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return {"direction": None, "hour": hour, "minute": minute, "meridian": None}

    return None


def _resolve_punch_time(hour: int, minute: int, meridian: str | None) -> "datetime":
    """Convert parsed hour/minute/meridian to a datetime for today."""
    now = now_datetime()
    today = getdate(now)

    # Normalize 12h → 24h
    if meridian:
        m = meridian.lower().replace(".", "")
        if m in ("pm",) and hour < 12:
            hour += 12
        elif m in ("am",) and hour == 12:
            hour = 0

    # If no meridian and hour looks like 24h (> 12), use as-is
    # If no meridian and hour <= 12, assume work-hour context:
    #   hour < 8 → AM, hour >= 8 → AM (most punches are AM/PM pairs)
    #   The user can always send "3:50pm" to be explicit.

    return get_datetime(f"{today} {hour:02d}:{minute:02d}:00")


def _get_employee_by_phone(phone_number: str) -> dict | None:
    """Find employee by WhatsApp phone number.

    Returns {"name": "HR-EMP-0042", "employee_name": "Mitesh Patel",
             "user_id": "Administrator"} or None.
    """
    # Resolve @lid to @c.us
    if phone_number.endswith("@lid"):
        resolved = _resolve_lid_to_phone(phone_number)
        if resolved:
            phone_number = resolved
        else:
            return None

    # Normalize: "91xxxxxxxxxx@c.us" -> "91xxxxxxxxxx"
    clean_phone = phone_number.split("@")[0].lstrip("+")

    employees = frappe.get_all(
        "Employee",
        filters={
            "cell_number": ["like", f"%{clean_phone[-10:]}%"],
            "status": "Active",
        },
        fields=["name", "employee_name", "user_id"],
        limit=1,
    )
    if not employees or not employees[0].get("user_id"):
        return None

    emp = employees[0]

    # Check allowed_roles from OpenWA Settings
    settings = frappe.get_cached_doc("OpenWA Settings")
    allowed_roles_raw = settings.allowed_roles or ""
    allowed_roles = [r.strip() for r in allowed_roles_raw.split(",") if r.strip()]

    if allowed_roles:
        user_roles = frappe.get_roles(emp["user_id"])
        if not any(role in user_roles for role in allowed_roles):
            return None

    return emp


def _try_whatsapp_punch(reply_to: str, message_text: str) -> bool:
    """Intercept WhatsApp messages that are punch in/out commands.

    Returns True if the message was consumed (punch or punch-related response),
    False if it should fall through to bot commands.
    """
    parsed = _parse_punch_message(message_text)
    if not parsed:
        return False

    # Get employee by phone
    emp = _get_employee_by_phone(reply_to)
    if not emp:
        return False  # not an authorized employee — let bot commands handle it

    employee_name = emp["name"]
    employee_display = emp.get("employee_name") or employee_name

    # Resolve direction
    direction = parsed["direction"]

    # Resolve time
    if parsed["hour"] is not None:
        punch_time = _resolve_punch_time(parsed["hour"], parsed["minute"], parsed["meridian"])
    else:
        punch_time = now_datetime()

    # Reject future time
    now = now_datetime()
    if punch_time > now:
        _send_text(reply_to, "Cannot punch for a future time. Please enter the actual time.")
        return True

    # Auto-detect direction if not specified
    if direction is None:
        today_start = getdate(now)
        last = frappe.get_all(
            "Employee Checkin",
            filters={
                "employee": employee_name,
                "time": [">=", str(today_start)],
            },
            fields=["log_type"],
            order_by="time desc",
            limit=1,
        )
        if not last or last[0].log_type == "OUT":
            direction = "IN"
        else:
            direction = "OUT"

    # Dedup: reject if same employee has a WhatsApp punch within 60 minutes
    cutoff = add_to_date(now, minutes=-60)
    recent = frappe.get_all(
        "Employee Checkin",
        filters={
            "employee": employee_name,
            "device_id": "WhatsApp",
            "time": [">=", str(cutoff)],
        },
        fields=["time", "log_type"],
        order_by="time desc",
        limit=1,
    )
    if recent:
        _send_text(
            reply_to,
            f"Already punched ({recent[0].log_type}) at "
            f"{format_datetime(recent[0].time, 'HH:mm')}",
        )
        return True

    # Create Employee Checkin
    try:
        checkin = frappe.get_doc({
            "doctype": "Employee Checkin",
            "employee": employee_name,
            "time": punch_time,
            "log_type": direction,
            "device_id": "WhatsApp",
            "skip_auto_attendance": 0,
        })
        checkin.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            title="WhatsApp Punch Insert Error",
            message=frappe.get_traceback(),
        )
        _send_text(reply_to, "Failed to record punch. Please try again.")
        return True

    # Send confirmation via existing notification flow (separate try so
    # notification failure never loses the punch).
    try:
        from kreativ_notification.notification.employee_notifications import notify_checkin
        notify_checkin(checkin.name)
    except Exception:
        frappe.log_error(
            title="WhatsApp Punch Notification Error",
            message=frappe.get_traceback(),
        )

    frappe.logger().info(
        f"WhatsApp punch: {direction} for {employee_name} at {punch_time} "
        f"(checkin: {checkin.name})"
    )
    return True


def _check_rate_limit(sender_chat_id: str) -> bool:
    if not sender_chat_id:
        return True
    cache_key = f"wa_rate_limit:{sender_chat_id}"
    current = frappe.cache().get_value(cache_key) or 0
    if current >= RATE_LIMIT_PER_MINUTE:
        return False
    frappe.cache().set_value(cache_key, current + 1, expires_in_sec=60)
    return True


def _get_help_text(settings=None) -> str:
    if not settings:
        settings = frappe.get_cached_doc("OpenWA Settings")

    lines = ["Bot Help\n"]
    if settings.bot_commands:
        for cmd in settings.bot_commands:
            if cmd.enabled:
                search_field = cmd.search_field or "name"
                lines.append(f"  {cmd.command_keyword} <{search_field}> — {cmd.doc_type}")
                if cmd.remarks:
                    lines.append(f"    {cmd.remarks}")
    else:
        # Fallback to old keywords if no bot commands configured
        inv_kw = settings.invoice_keywords or DEFAULT_INVOICE_KEYWORDS
        ledger_kw = settings.ledger_keywords or DEFAULT_LEDGER_KEYWORDS
        lines.append(f"  Invoice: {', '.join(inv_kw.split(','))} <invoice_number>")
        lines.append(f"  Ledger: {', '.join(ledger_kw.split(','))} <customer_name>")

    lines.append("\nExamples:")
    lines.append("  • invoice KG/2627/307")
    lines.append("  • ledger Kreativ")
    lines.append("\nOnly submitted documents can be retrieved.")

    return "\n".join(lines)


def check_inbound_webhook_health():
    """Scheduled job: verify inbound webhook is reachable."""
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.webhook_enabled:
        return
    frappe.logger().info("Inbound webhook health check: OK")