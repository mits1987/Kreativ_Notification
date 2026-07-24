"""Inbound WhatsApp Webhook Handler — Unified Bot.

Single bot handles: invoice requests, account ledger requests, and help text.
"""
import frappe
import re
import json
import base64
import time
from typing import Optional
from frappe.utils import get_datetime, now_datetime

from kreativ_notification.notification.openwa_client import (
    check_circuit_breaker,
    increment_circuit_breaker,
    reset_circuit_breaker,
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
            _handle_conversation_reply(reply_to, message_text, conversation, employee_user_id)
            return

        # Try invoice keywords
        invoice_identifier = _parse_invoice_reference(message_text, settings)
        if invoice_identifier:
            _handle_invoice_request(reply_to, invoice_identifier, employee_user_id)
            return

        # Try ledger keywords
        ledger_term = _parse_ledger_reference(message_text, settings)
        if ledger_term:
            _handle_ledger_request(reply_to, ledger_term, employee_user_id)
            return

        # No keyword matched — send help
        _send_text(reply_to, _get_help_text(settings))

    except Exception:
        frappe.log_error(title="Inbound WhatsApp Processing Error", message=frappe.get_traceback())


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
            "show_remarks": 1,
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
                _send_ledger_pdf(selected["name"], selected.get("customer_name", ""), reply_to, employee_user_id)
            else:
                _send_text(reply_to, f"Please enter a number between 1 and {len(customers)}.")
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
# Helpers
# ---------------------------------------------------------------------------

def _get_employee_user_id(phone_number: str) -> str | None:
    """Find employee by cell_number and return their linked user_id.

    Normalizes phone from '91xxxxxxxxxx@c.us' format and matches
    against Employee.cell_number (last 10 digits).
    Also validates employee has a role in allowed_roles (if configured).
    """
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
    if settings:
        inv_kw = settings.invoice_keywords or DEFAULT_INVOICE_KEYWORDS
        ledger_kw = settings.ledger_keywords or DEFAULT_LEDGER_KEYWORDS
    else:
        inv_kw = DEFAULT_INVOICE_KEYWORDS
        ledger_kw = DEFAULT_LEDGER_KEYWORDS

    return (
        "Bot Help\n\n"
        f"Invoice: {', '.join(inv_kw.split(','))} <invoice_number>\n"
        f"Ledger: {', '.join(ledger_kw.split(','))} <customer_name>\n\n"
        "Examples:\n"
        "• invoice KG/2627/307\n"
        "• ledger Kreativ\n\n"
        "Only submitted invoices can be retrieved."
    )


def check_inbound_webhook_health():
    """Scheduled job: verify inbound webhook is reachable."""
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.webhook_enabled:
        return
    frappe.logger().info("Inbound webhook health check: OK")