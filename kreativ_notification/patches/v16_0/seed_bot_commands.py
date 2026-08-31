"""Seed WhatsApp Bot Commands on fresh install."""

import frappe


def execute():
    """Create default bot commands if they don't exist."""
    # Define the standard bot commands for a local installation
    # api_link = "" means local site fetch; users can override for remote
    default_commands = [
        {
            "name": "invoice",
            "command_keyword": "invoice,inv,bill,बिल",
            "doc_type": "Sales Invoice",
            "print_format": "EINVOICE TALLY",
            "fetch_type": "Document",
            "doc_status": "1",
            "auth_type": "None",
            "search_field": "name",
            "allowed_roles": "Sales Manager,Sales User,Marketing User",
            "api_link": "",
            "remarks": "Invoice PDF via EINVOICE TALLY format. For remote fetch, set api_link and auth_type.",
        },
        {
            "name": "order",
            "command_keyword": "order",
            "doc_type": "Sales Order",
            "print_format": "JobCard",
            "fetch_type": "Document",
            "doc_status": "0,1",
            "auth_type": "None",
            "search_field": "name",
            "allowed_roles": "Sales Manager,Sales User,Marketing User",
            "api_link": "",
            "remarks": "Sales Order PDF via JobCard format. Supports Draft (0) and Submitted (1).",
        },
        {
            "name": "dn",
            "command_keyword": "DN,D",
            "doc_type": "Delivery Note",
            "print_format": "Combined DN Invoice",
            "fetch_type": "Document",
            "doc_status": "1",
            "auth_type": "None",
            "search_field": "name",
            "allowed_roles": "Sales Manager,Sales User,Marketing User",
            "api_link": "",
            "remarks": "Delivery Note PDF via Combined DN Invoice format. 'D' is shorthand.",
        },
        {
            "name": "ledger",
            "command_keyword": "ledger",
            "doc_type": "Customer",
            "print_format": "",
            "fetch_type": "Report",
            "doc_status": "1",
            "auth_type": "None",
            "search_field": "name",
            "allowed_roles": "Sales Manager,Sales User,Marketing User",
            "api_link": "",
            "remarks": "Customer General Ledger report. For remote fetch, set api_link to remote server URL and auth_type to Token.",
        },
    ]

    for cmd_data in default_commands:
        cmd_data.pop("name", None)
        # Check by first keyword to avoid duplicates (auto-generated names differ)
        first_keyword = cmd_data["command_keyword"].split(",")[0].strip()
        if not frappe.db.exists("WhatsApp Bot Command", {"command_keyword": ["like", f"%{first_keyword}%"]}):
            doc = frappe.get_doc({
                "doctype": "WhatsApp Bot Command",
                **cmd_data,
            })
            doc.insert(ignore_permissions=True)
            print(f"Created bot command: {first_keyword}")
        else:
            print(f"Bot command already exists: {first_keyword}")

    frappe.db.commit()