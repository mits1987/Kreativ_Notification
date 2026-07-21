# ---------------------------------------------------------------------------
# MERGE NOTE: this file was reconstructed during the WhatsApp-stack
# consolidation. Before replacing your current hooks.py, diff it against
# this one — if your version has extra sections not shown here (fixtures,
# website context, extra patches), KEEP those sections and merge these
# changes in. Everything below that is new/changed is marked with  # NEW /
# # CHANGED comments.
# ---------------------------------------------------------------------------

app_name = "kreativ_notification"
app_title = "Kreativ Notification"
app_publisher = "Mitesh"
app_description = "Multi-channel notification platform for ERPNext (WhatsApp, Email, extensible)"
app_email = "info@kreativ.com"
app_license = "MIT"

# Desk JS
# Cache busting: bump ?v= date when pushing JS changes through Cloudflare.
# Frappe v16 include_script() does NOT auto-append a version query, so we add it manually.
app_include_js = [
    "/assets/kreativ_notification/js/kreativ_notification.js?v=20260720",
    "/assets/kreativ_notification/js/print_whatsapp_v4.js?v=20260720",
]

# Force import of API module before every request to ensure whitelisted methods are registered.
# This is needed because gunicorn workers (even with --preload) don't import app modules
# until they're needed. The @frappe.whitelist() decorator only executes on first import.
before_request = [
    "kreativ_notification.ensure_api_loaded",
]

# ---------------------------------------------------------------------------
# Doc events
#
# 1. Rules engine — ONE generic handler for no-code Notification Rules.
# 2. Employee Checkin / Salary Slip — the platform now OWNS these hooks.   # NEW
#    They used to be wired from kreativ_attendance/hooks.py, which created
#    a cross-app path ("kreativ_notification.notification.hooks.*") that
#    broke whenever the two apps were installed independently. Each app
#    now wires only its own handlers; Frappe merges doc_events across
#    installed apps, so attendance recalc AND notification both fire.
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
        # fallback channel escalation + channel health
        "*/5 * * * *": [
            "kreativ_notification.notification.dispatcher.process_fallbacks",
            "kreativ_notification.kreativ_notification.doctype.notification_channel.notification_channel.check_all_channels",
        ],
        # NEW — moved here from kreativ_attendance/hooks.py. Safety net for
        # checkin notifications whose original enqueue was lost. Transport
        # retries are handled by the dispatcher; this only re-feeds punches
        # that never reached dispatch(). Idempotent (dispatch dedupes on
        # checkin:{name}).
        "*/10 * * * *": [
            "kreativ_notification.notification.employee_notifications.retry_missed_notifications",
        ],
    },
    "daily": [
        # Days Before / Days After rules (payment reminders etc.)
        "kreativ_notification.notification.rules_engine.evaluate_date_rules",
    ],
}

# Install
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
    {"dt": "WhatsApp Send Log"},
]

# Extensibility — other apps can add channel drivers and bot commands
# via these hooks in THEIR hooks.py:
#
#   notification_channel_drivers = {"Telegram": "my_app.drivers.TelegramDriver"}
#   whatsapp_bot_commands = ["my_app.bot.leave_balance_command"]