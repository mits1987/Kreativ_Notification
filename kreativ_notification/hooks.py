app_name = "kreativ_notification"
app_title = "Kreativ Notification"
app_publisher = "Mitesh"
app_description = "Multi-channel notification platform for ERPNext (WhatsApp, Email, extensible)"
app_email = "info@kreativ.com"
app_license = "MIT"

# Desk JS
# Cache busting: rename file + update this list when pushing changes through Cloudflare.
# See feedback-cloudflare-cache-rocks in MEMORY.md.
app_include_js = [
    "/assets/kreativ_notification/js/kreativ_notification.js",
    "/assets/kreativ_notification/js/print_whatsapp_v4.js",
]

# ---------------------------------------------------------------------------
# Rules engine — ONE generic handler replaces per-doctype hardcoded hooks.
# The old Salary Slip / Employee Checkin hooks are now shipped as
# Notification Rule fixtures (see README) instead of Python.
# ---------------------------------------------------------------------------
doc_events = {
    "*": {
        "after_insert": "kreativ_notification.notification.rules_engine.handle_doc_event",
        "on_submit": "kreativ_notification.notification.rules_engine.handle_doc_event",
        "on_cancel": "kreativ_notification.notification.rules_engine.handle_doc_event",
        "on_update": "kreativ_notification.notification.rules_engine.handle_doc_event",
        "on_update_after_submit": "kreativ_notification.notification.rules_engine.handle_doc_event",
    },
    "Notification Rule": {
        "on_update": "kreativ_notification.notification.rules_engine.clear_rule_cache",
        "on_trash": "kreativ_notification.notification.rules_engine.clear_rule_cache",
    },
}

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
scheduler_events = {
    "cron": {
        # dispatcher safety net: due retries + stuck rows
        "*/2 * * * *": [
            "kreativ_notification.notification.dispatcher.process_due_retries",
        ],
        # fallback channel escalation
        "*/5 * * * *": [
            "kreativ_notification.notification.dispatcher.process_fallbacks",
            "kreativ_notification.kreativ_notification.doctype.notification_channel.notification_channel.check_all_channels",
        ],
    },
    "daily": [
        # Days Before / Days After rules (payment reminders etc.)
        "kreativ_notification.notification.rules_engine.evaluate_date_rules",
        # log retention
        "kreativ_notification.notification.dispatcher.cleanup_old_logs",
    ],
}

# Document Events
doc_events = {
    "Notification Rule": {
        "on_update": "kreativ_notification.notification.rules_engine.clear_rule_cache",
        "on_trash": "kreativ_notification.notification.rules_engine.clear_rule_cache",
    },
}

# Custom Fields
custom_fields = {
    "Employee Checkin": [
        {
            "fieldname": "whatsapp_sent",
            "label": "WhatsApp Sent",
            "fieldtype": "Int",
            "insert_after": "log_type",
            "read_only": 1,
            "no_copy": 1,
            "default": 0,
            "description": "0=not sent, 1=sent, 2=failed (retry), 3=invalid number (stop)",
        },
        {
            "fieldname": "whatsapp_retry_count",
            "label": "WhatsApp Retry Count",
            "fieldtype": "Int",
            "insert_after": "whatsapp_sent",
            "read_only": 1,
            "no_copy": 1,
            "default": 0,
            "description": "Number of times WhatsApp send has been attempted",
        },
    ],
}

# Install hooks
after_install = "kreativ_notification.install.after_install"

# Patches
patches = [
    "kreativ_notification.patches.v16_0.migrate_openwa_settings_fields",
    "kreativ_notification.patches.v16_0.migrate_whatsapp_send_log",
    "kreativ_notification.patches.v16_0.add_platform_fields_to_send_log",
]

# Fixtures — ship the default rules/templates that replace the old
# hardcoded Salary Slip + Employee Checkin behavior
fixtures = [
    {"dt": "Message Template", "filters": [["module", "=", "Kreativ Notification"]]},
    {"dt": "Notification Rule", "filters": [["module", "=", "Kreativ Notification"]]},
]

# Extensibility — other apps can add channel drivers and bot commands
# via these hooks in THEIR hooks.py:
#
#   notification_channel_drivers = {"Telegram": "my_app.drivers.TelegramDriver"}
#   whatsapp_bot_commands = ["my_app.bot.leave_balance_command"]
