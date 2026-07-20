# Kreativ Notification

WhatsApp integration for ERPNext v16 — outbound notifications, inbound auto-reply bot, and Print Preview "Send to WhatsApp" button.

## Current Architecture (v16 — legacy)

```
kreativ_notification/
├── notification/
│   ├── inbound.py              # Webhook endpoint for OpenWA inbound messages
│   ├── openwa_client.py        # OpenWA HTTP client + circuit breaker
│   ├── send.py                 # High-level send API (PDF, image, text, test)
│   ├── send_log.py             # WhatsApp Send Log helpers
│   ├── health.py               # 1-min health check + auto-recovery
│   ├── pdf_utils.py            # PDF generation (base64 images, strip toolbar)
│   ├── screenshot_utils.py     # Playwright headless Chromium screenshots
│   ├── queue.py                # Notification Queue flush/retry
│   ├── employee_notifications.py  # Checkin + Salary Slip WhatsApp sends
│   └── contacts.py             # Phone validation + normalization
├── doctype/
│   ├── openwa_settings/        # OpenWA gateway config (Single)
│   │   └── webhook_enabled, webhook_secret, auto_reply_enabled,
│   │       allowed_roles, invoice_keywords, ledger_keywords
│   └── whatsapp_send_log/      # Audit trail for all outbound sends
└── public/js/print_whatsapp_v4.js  # Print Preview toolbar button + contact picker
```

## Features

| Feature | Status |
|---------|--------|
| **Outbound WhatsApp** | ✅ Via OpenWA gateway (self-hosted) |
| **Employee Checkin notifications** | ✅ IN/OUT alerts with shift hours |
| **Salary Slip WhatsApp delivery** | ✅ PDF rendered + sent on submit |
| **Print Preview "Send to WhatsApp"** | ✅ Contact picker modal, search, manual entry |
| **Inbound auto-reply bot** | ✅ `inbound.py` handles `invoice <ref>` → PDF reply |
| **Employee phone validation** | ✅ Only active employees with allowed roles |
| **Rate limiting** | ✅ 10 req/min per sender (cache) |
| **Circuit breaker** | ✅ Per-site, 3 failures → exponential backoff |
| **Auto-recovery** | ✅ Session 404→recreate, disconnected→start, stale→restart |
| **WhatsApp Send Log** | ✅ Full audit trail (status, error, retry count) |

## Inbound Bot Flow

1. OpenWA posts to `/api/method/kreativ_notification.notification.inbound.receive_whatsapp_message`
2. HMAC-SHA256 verified via `webhook_secret` in OpenWA Settings
3. Sender phone extracted from `chat_id` (format: `919876543210@c.us` or `@lid`)
4. Matched against `Employee.cell_number` (last 10 digits) + must have role in `allowed_roles`
5. Message parsed for invoice keywords (`invoice`, `inv`, `बिल`) + reference (supports `KG/2627/307`)
6. Sales Invoice fetched (`docstatus=1`, exact name match or custom field)
7. PDF generated via `frappe.get_print(..., as_pdf=True)` with base64 image inlining
8. Sent via `_send_document_via_whatsapp()` from `gravures_custom.overrides`
9. Logged to `WhatsApp Send Log`

## Quick Test (kreativ316)

```bash
# 1. Enable webhook in OpenWA Settings
#    webhook_enabled=1, webhook_secret=<your_secret>, auto_reply_enabled=1
#    allowed_roles: "Sales Manager, Sales User, Marketing User"

# 2. Configure OpenWA gateway webhook:
#    POST https://kreativ316.example.com/api/method/kreativ_notification.notification.inbound.receive_whatsapp_message
#    Header: X-OpenWA-Signature: sha256=<HMAC of raw body>

# 3. From employee WhatsApp (e.g. 9023587002) send to bot (919106526195):
#    "inv KG/2627/307"

# 4. Check WhatsApp Send Log — should show "Sent" with PDF
```

## Key Backend Functions (`gravures_custom.overrides`)

| Function | Purpose |
|----------|---------|
| `_generate_pdf_bytes(doctype, name, print_format)` | Headless Chromium PDF, base64 images, strips action-banner |
| `validate_phone_number(phone)` | phonenumbers lib validation, returns `{valid, formatted, e164, chat_id}` |
| `_send_document_via_whatsapp(chat_id, b64, filename, caption)` | OpenWA send-document endpoint |
| `get_whatsapp_chats(search)`, `search_whatsapp_contacts(query)` | Contact picker for Print Preview |
| `send_print_pdf_whatsapp(doctype, name, print_format, chat_id)` | Print Preview button handler |

## Scheduled Jobs (`hooks.py`)

| Schedule | Function |
|----------|----------|
| `*/1 * * * *` | `check_openwa_session` (health + auto-recovery) |
| `*/2 * * * *` | `retry_missed_notifications` (checkin/salary slip retry) |
| `daily` | `cleanup_old_sessions` (session hygiene) |

## Multi-Site Notes

- **kreativ216 (PROD)** — OpenWA gateway shared, manual restart only (`/home/mitesh/OpenWA/restart.sh`)
- **kreativ316 (TEST)** — Same gateway, separate session, scheduler running
- Circuit breaker keyed per-site: `openwa_failure_streak:{site}` in `frappe.cache()`

## Known Duplication (to consolidate later)

| Functionality | kreativ_notification | kreativ_attendance | gravures_custom |
|---------------|---------------------|-------------------|-----------------|
| OpenWA HTTP client | `openwa_client.py` | — | `whatsapp_queue.py` |
| Circuit breaker | `openwa_client.py` | `health.py` | `whatsapp_queue.py` |
| Send PDF/Image/Text | `send.py` | `whatsapp.py` | `__init__.py` (7 dashboard senders) |
| Phone validation | `contacts.py` | `whatsapp.py` | `__init__.py` |
| Health check | `health.py` | `openwa_health.py` | — |
| Send Log | `send_log.py` + doctype | — | — |

---

## Platform Upgrade (staged in `kreativ_notification_platform.zip`)

A complete rewrite turning this into a **multi-channel notification platform**:

- Pluggable channel drivers: OpenWA, Meta Cloud API, Email (extensible via hook)
- No-code `Notification Rule` (doc events + Days Before/After date fields)
- Jinja `Message Template` with language variants + PDF attachment
- Single `dispatch()` pipeline: idempotency, outbox, retry/backoff, quiet hours, rate limit, fallback channels
- Delivery receipts (`Delivered`/`Read`) via `record_delivery_status()`
- New doctypes: `Notification Channel`, `Notification Rule`, `Message Template`, `Message Template Variant`

**README from platform upgrade:** `PLATFORM_README.md` (on Desktop) or inside the zip.

Migration steps when ready:
1. Copy platform files over app (diff `hooks.py` first)
2. `bench migrate && bench --site kreativ316 execute kreativ_notification.notification.setup_defaults.run`
3. Delete old hardcoded hooks in `kreativ_attendance` and `gravures_custom`
4. Enable bootstrap rules (Salary Slip, Checkin) — they ship disabled