app_name = "kreativ_notification"
app_title = "Kreativ Notification"
app_publisher = "Mitesh"
app_description = "Unified WhatsApp/notification infrastructure for Kreativ Gravures"
app_email = "info@kreativ.com"
app_license = "MIT"

# Desk JS
# Cache busting: rename file + update this list when pushing changes through Cloudflare.
# See feedback-cloudflare-cache-rocks in MEMORY.md.
app_include_js = [
    "/assets/kreativ_notification/js/kreativ_notification.js",
    "/assets/kreativ_notification/js/print_whatsapp.js",
    "/assets/kreativ_notification/js/print_whatsapp_v4.js",
]

# Scheduled Tasks
scheduler_events = {
    "all": [
        "kreativ_notification.notification.queue.flush_outgoing",
        "kreativ_notification.notification.queue.retry_failed",
    ],
    "cron": {
        "*/5 * * * *": [
            "kreativ_notification.notification.health.check_openwa_session",
            "kreativ_notification.notification.health.check_inbound_webhook_health",
        ],
        "*/10 * * * *": [
            "kreativ_notification.notification.employee_notifications.retry_missed_notifications",
        ],
    },
}

# Document Events
doc_events = {
    "Salary Slip": {
        "on_submit": [
            "kreativ_notification.notification.salary_slip_hooks.on_salary_slip_whatsapp",
        ]
    },
    "Employee Checkin": {
        "on_change": "kreativ_notification.notification.hooks.on_checkin_updated",
        "on_trash": "kreativ_notification.notification.hooks.on_checkin_trashed",
        "after_insert": "kreativ_notification.notification.hooks.on_checkin_created",
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
]