"""Driver registry.

Other apps can register drivers via hooks.py:

    notification_channel_drivers = {
        "Telegram": "my_app.drivers.TelegramDriver",
    }
"""

from __future__ import annotations

import frappe

# Built-in drivers. Keys must match the Notification Channel `channel_type` options.
BUILTIN_DRIVERS = {
    "WhatsApp - OpenWA": "kreativ_notification.notification.channels.openwa.OpenWADriver",
    "WhatsApp - Meta Cloud API": "kreativ_notification.notification.channels.meta_cloud.MetaCloudDriver",
    "Email": "kreativ_notification.notification.channels.email_channel.EmailDriver",
}


def get_driver_map() -> dict:
    driver_map = dict(BUILTIN_DRIVERS)
    # Allow other installed apps to add/override drivers
    for hook_map in frappe.get_hooks("notification_channel_drivers") or []:
        if isinstance(hook_map, dict):
            driver_map.update(hook_map)
    return driver_map


def get_driver(channel_name: str):
    """Return an instantiated driver for a Notification Channel name."""
    channel = frappe.get_cached_doc("Notification Channel", channel_name)
    if not channel.enabled:
        frappe.throw(f"Notification Channel '{channel_name}' is disabled.")

    path = get_driver_map().get(channel.channel_type)
    if not path:
        frappe.throw(f"No driver registered for channel type '{channel.channel_type}'.")

    driver_cls = frappe.get_attr(path)
    return driver_cls(channel)


def get_default_channel(channel_type: str | None = None) -> str | None:
    """Return the default enabled channel (optionally of a given type)."""
    filters = {"enabled": 1}
    if channel_type:
        filters["channel_type"] = channel_type

    default = frappe.get_all(
        "Notification Channel",
        filters={**filters, "is_default": 1},
        pluck="name", limit_page_length=1,
    )
    if default:
        return default[0]

    any_enabled = frappe.get_all(
        "Notification Channel", filters=filters,
        order_by="priority asc, creation asc",
        pluck="name", limit_page_length=1,
    )
    return any_enabled[0] if any_enabled else None
