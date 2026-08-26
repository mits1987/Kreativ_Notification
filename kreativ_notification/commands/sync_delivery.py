import frappe
from kreativ_notification.notification.dispatcher import sync_delivery_status

def execute():
    result = sync_delivery_status()
    print(f"Synced: {result}")
