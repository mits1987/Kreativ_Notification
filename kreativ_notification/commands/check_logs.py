import frappe

def execute():
    logs = frappe.get_all("WhatsApp Send Log", 
        fields=["name", "status", "delivery_synced", "creation", "recipient", "source_doctype", "channel", "source_docname"],
        filters={"source_doctype": "Employee Checkin"},
        order_by="creation desc", 
        limit=20)
    for l in logs:
        print(l)
