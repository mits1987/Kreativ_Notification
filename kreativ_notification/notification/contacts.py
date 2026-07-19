"""Contact utilities."""
import frappe
import re


def validate_phone_number(phone: str, default_country_code: str = "91") -> dict:
    """Validate and format phone number for WhatsApp.

    Returns dict with: valid, formatted, e164, chat_id, error
    """
    if not phone:
        return {"valid": False, "error": "Empty phone number"}

    digits = "".join(filter(str.isdigit, phone))

    if len(digits) < 10:
        return {"valid": False, "error": f"Phone number too short: {digits}"}

    # If 10 digits, prepend country code
    if len(digits) == 10 and default_country_code:
        if not digits.startswith(default_country_code):
            digits = default_country_code + digits

    # If already has country code but wrong length
    if len(digits) > 13:
        return {"valid": False, "error": f"Phone number too long: {digits}"}

    e164 = "+" + digits
    chat_id = digits + "@c.us"

    return {
        "valid": True,
        "formatted": digits,
        "e164": e164,
        "chat_id": chat_id,
    }


def normalize_phone(phone: str, default_country_code: str = "91") -> str:
    """Normalize phone to WhatsApp chat_id format (digits@c.us)."""
    result = validate_phone_number(phone, default_country_code)
    if result["valid"]:
        return result["chat_id"]
    return ""