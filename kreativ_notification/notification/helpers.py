"""Helper utilities."""
import frappe
import json


def format_date_range(from_date, to_date) -> str:
    """Format date range for display."""
    if from_date and to_date:
        return f"{frappe.utils.format_date(from_date)} to {frappe.utils.format_date(to_date)}"
    elif from_date:
        return f"From {frappe.utils.format_date(from_date)}"
    elif to_date:
        return f"Until {frappe.utils.format_date(to_date)}"
    return "All dates"


def format_month(date_obj) -> str:
    """Format date as 'Month YYYY'."""
    if date_obj:
        return frappe.utils.format_date(date_obj, "MMMM yyyy")
    return ""