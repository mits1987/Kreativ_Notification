"""Permission utilities for WhatsApp features."""
import frappe


def has_whatsapp_access(user: str = None) -> bool:
    """Check if user has WhatsApp User or WhatsApp Manager role."""
    if not user:
        user = frappe.session.user
    roles = frappe.get_roles(user)
    return "WhatsApp User" in roles or "WhatsApp Manager" in roles


def can_send_whatsapp(user: str = None) -> bool:
    """Check if user can send WhatsApp messages."""
    return has_whatsapp_access(user)


def can_manage_whatsapp(user: str = None) -> bool:
    """Check if user can manage WhatsApp settings."""
    if not user:
        user = frappe.session.user
    roles = frappe.get_roles(user)
    return "WhatsApp Manager" in roles or "System Manager" in roles