"""Seed WhatsApp Bot Commands on fresh install only."""

import frappe


def execute():
    """Create default bot commands only if the table is completely empty."""
    if frappe.db.exists("WhatsApp Bot Command"):
        return

    default_commands = [
        {
            "command_keyword": "invoice,inv,bill,बिल",
            "doc_type": "Sales Invoice",
            "print_format": "EINVOICE TALLY",
            "fetch_type": "Document",
            "doc_status": "1",
            "auth_type": "None",
            "search_field": "name",
            "allowed_roles": "Sales Manager,Sales User,Marketing User",
            "api_link": "",
            "remarks": "Invoice PDF via EINVOICE TALLY format.",
        },
        {
            "command_keyword": "order",
            "doc_type": "Sales Order",
            "print_format": "JobCard",
            "fetch_type": "Document",
            "doc_status": "0,1",
            "auth_type": "None",
            "search_field": "name",
            "allowed_roles": "Sales Manager,Sales User,Marketing User",
            "api_link": "",
            "remarks": "Sales Order PDF via JobCard format.",
        },
        {
            "command_keyword": "DN,D",
            "doc_type": "Delivery Note",
            "print_format": "Combined DN Invoice",
            "fetch_type": "Document",
            "doc_status": "1",
            "auth_type": "None",
            "search_field": "name",
            "allowed_roles": "Sales Manager,Sales User,Marketing User",
            "api_link": "",
            "remarks": "Delivery Note PDF via Combined DN Invoice format.",
        },
        {
            "command_keyword": "ledger",
            "doc_type": "Customer",
            "print_format": "",
            "fetch_type": "Report",
            "doc_status": "1",
            "auth_type": "None",
            "search_field": "name",
            "allowed_roles": "Sales Manager,Sales User,Marketing User",
            "api_link": "",
            "remarks": "Customer General Ledger report.",
        },
        {
            "command_keyword": "outstanding,outstanding report,beat,baki",
            "doc_type": "Customer",
            "print_format": "",
            "fetch_type": "Report",
            "doc_status": "1",
            "auth_type": "None",
            "search_field": "name",
            "allowed_roles": "Sales Manager,Sales User,Marketing User",
            "api_link": "",
            "remarks": "Account Receivable report for customer.",
        },
        {
            "command_keyword": "payable,payable report,supplier outstanding",
            "doc_type": "Supplier",
            "print_format": "",
            "fetch_type": "Report",
            "doc_status": "1",
            "auth_type": "None",
            "search_field": "name",
            "allowed_roles": "Accounts Manager,Accounts User,Purchase Manager,Purchase User",
            "api_link": "",
            "remarks": "Account Payable report for supplier.",
        },
    ]

    for cmd_data in default_commands:
        doc = frappe.get_doc({"doctype": "WhatsApp Bot Command", **cmd_data})
        doc.insert(ignore_permissions=True)
        print(f"Seeded bot command: {cmd_data['command_keyword'].split(',')[0].strip()}")

    frappe.db.commit()
