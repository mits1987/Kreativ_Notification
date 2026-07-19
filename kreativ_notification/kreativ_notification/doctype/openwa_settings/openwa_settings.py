# -*- coding: utf-8 -*-
# Copyright (c) 2026, Kreativ Gravures and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document


class OpenWASettings(Document):
    pass


@frappe.whitelist()
def get_session_status():
    """Fetch current session status from OpenWA gateway."""
    from kreativ_notification.notification.openwa_client import OpenWAClient
    client = OpenWAClient()
    return client.get_session_status()


@frappe.whitelist()
def get_session_qr():
    """Get QR code image for the session."""
    from kreativ_notification.notification.openwa_client import OpenWAClient
    client = OpenWAClient()
    return client.get_session_qr()


@frappe.whitelist()
def start_session():
    """Start/Restart the WhatsApp session."""
    from kreativ_notification.notification.openwa_client import OpenWAClient
    client = OpenWAClient()
    return client.start_session()


@frappe.whitelist()
def stop_session():
    """Stop the WhatsApp session."""
    from kreativ_notification.notification.openwa_client import OpenWAClient
    client = OpenWAClient()
    return client.stop_session()


@frappe.whitelist()
def create_new_session():
    """Create a new session on OpenWA and update settings."""
    from kreativ_notification.notification.openwa_client import OpenWAClient
    client = OpenWAClient()
    return client.create_session()


@frappe.whitelist()
def validate_phone_number(phone: str, default_country: str = "IN") -> dict:
    """Validate and format a phone number for WhatsApp."""
    from kreativ_notification.notification.contacts import validate_phone_number as _validate
    return _validate(phone, default_country)


@frappe.whitelist()
def get_whatsapp_chats(search: str = None) -> dict:
    """Fetch recent chats from OpenWA for the contact picker."""
    from kreativ_notification.notification.openwa_client import OpenWAClient
    client = OpenWAClient()
    return client.get_chats(search)


@frappe.whitelist()
def search_whatsapp_contacts(query: str) -> dict:
    """Search contacts via OpenWA chats endpoint."""
    from kreativ_notification.notification.openwa_client import OpenWAClient
    client = OpenWAClient()
    return client.search_contacts(query)


@frappe.whitelist()
def send_print_pdf_whatsapp(doctype: str, name: str, print_format: str = None, chat_id: str = None):
    """Send a document's PDF to WhatsApp."""
    from kreativ_notification.notification.send import send_document_via_whatsapp
    settings = frappe.get_cached_doc("OpenWA Settings")
    return send_document_via_whatsapp(
        base64_pdf="",
        filename="{}.pdf".format(name),
        caption="{}".format(name),
        chat_id_override=chat_id,
        source_doctype=doctype,
        source_docname=name,
        source_print_format=print_format or "Standard",
    )