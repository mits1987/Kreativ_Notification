# Kreativ Notification

WhatsApp integration for ERPNext v16 — outbound notifications, inbound auto-reply bot, and Print Preview "Send to WhatsApp" button.

**Current Version:** Platform-ready (v2) with multi-channel support, pluggable drivers, and no-code rules engine.

---

## 🚀 Platform Features (v2 — IMPLEMENTED)

| Feature | Status |
|---------|--------|
| **Multi-Channel Architecture** | ✅ Pluggable drivers via `channels/` module |
| **OpenWA Driver** | ✅ `channels/openwa.py` — self-hosted WhatsApp |
| **Meta Cloud API Driver** | ✅ `channels/meta_cloud.py` — official Business API |
| **Email Driver** | ✅ `channels/email_channel.py` — first-class email channel |
| **Driver Registry** | ✅ Hook-extensible (`notification_channel_drivers`) |
| **Notification Rules** | ✅ No-code rules for doc events + date-based triggers |
| **Message Templates** | ✅ Jinja templates with language variants |
| **Dispatcher Pipeline** | ✅ Idempotency, outbox, retry/backoff, quiet hours |
| **Fallback Channels** | ✅ Auto-escalation (WhatsApp → Email after N minutes) |
| **Circuit Breaker** | ✅ Per-channel failure tracking |
| **Rate Limiting** | ✅ Per-channel rate limits |
| **Exponential Backoff** | ✅ 5 retry attempts (1, 5, 15, 60, 180 min) |
| **Atomic Claim Pattern** | ✅ Prevents double delivery |
| **Scheduler Integration** | ✅ Cron jobs for retries, fallbacks, date rules |

---

## 📋 Future Enhancements (NOT YET IMPLEMENTED)

### 1. Consent Registry (HIGH PRIORITY — Legal Compliance)

**What:** Track opt-in/opt-out status per contact per channel.

**Why Required:**
- GDPR/TCPA compliance (legal requirement in EU/US)
- WhatsApp Business Policy requires explicit consent
- Reduce spam complaints and improve deliverability

**Implementation Needed:**

**A. New Doctype: `Consent Registry`**
```json
{
  "doctype": "Consent Registry",
  "fields": [
    {"fieldname": "contact", "fieldtype": "Link", "options": "Contact"},
    {"fieldname": "channel", "fieldtype": "Link", "options": "Notification Channel"},
    {"fieldname": "status", "fieldtype": "Select", "options": "Opted In\nOpted Out"},
    {"fieldname": "opt_in_date", "fieldtype": "Datetime"},
    {"fieldname": "opt_out_date", "fieldtype": "Datetime"},
    {"fieldname": "opt_in_reason", "fieldtype": "Small Text"},
    {"fieldname": "opt_out_reason", "fieldtype": "Small Text"},
    {"fieldname": "ip_address", "fieldtype": "Data"},
    {"fieldname": "user_agent", "fieldtype": "Data"}
  ],
  "indexes": [
    {"fields": ["contact", "channel"], "unique": true}
  ]
}
```

**B. Dispatcher Integration**
Add consent check at start of `dispatch()` in `dispatcher.py`:
```python
# Check consent before sending
consent = frappe.db.get_value("Consent Registry",
    {"contact": recipient, "channel": channel}, "status")

if consent == "Opted Out":
    return {"success": False, "error": "Recipient opted out", "blocked_by_consent": True}

if consent != "Opted In":
    return {"success": False, "error": "No consent recorded", "blocked_by_consent": True}
```

**C. Inbound Bot STOP/START Commands**
Add to `inbound.py`:
```python
if message_text.strip().upper() == "STOP":
    # Create Consent Registry record with status="Opted Out"
    # Reply: "You have been unsubscribed. Reply START to re-enable."

if message_text.strip().upper() == "START":
    # Create Consent Registry record with status="Opted In"
    # Reply: "You have been re-subscribed."
```

**D. Email Unsubscribe Links**
Add to email templates in `email_channel.py`:
```html
<small><a href="{{ unsubscribe_url }}">Unsubscribe</a></small>
```

**Files to Create/Modify:**
- [ ] `kreativ_notification/kreativ_notification/doctype/consent_registry/` (new doctype)
- [ ] `dispatcher.py` — add consent check
- [ ] `inbound.py` — parse STOP/START commands
- [ ] `email_channel.py` — add unsubscribe links
- [ ] Patch to migrate existing contacts to "Opted In" by default (or require manual opt-in)

---

### 2. Template Language Variants (MEDIUM PRIORITY — UX Improvement)

**What:** Auto-select message template language based on recipient's preference.

**Current State:**
- ✅ `Message Template Variant` doctype exists with `language` field
- ❌ Dispatcher doesn't auto-detect recipient language
- ❌ No Jinja rendering for variants

**Implementation Needed:**

**A. Language Detection Function**
Add to `dispatcher.py`:
```python
def _get_recipient_language(recipient: str, source_doctype: str, source_docname: str) -> str:
    """Auto-detect from Contact/Employee/Customer language field."""
    # 1. Try source document's linked record
    if source_docname:
        doc = frappe.get_cached_doc(source_doctype, source_docname)
        for field in ["customer", "supplier", "employee", "contact"]:
            if hasattr(doc, field) and getattr(doc, field):
                linked = frappe.get_cached_doc(doc.doctype, getattr(doc, field))
                if hasattr(linked, "language") and linked.language:
                    return linked.language
    
    # 2. Try to find Contact by phone/email
    contact = None
    if "@" in recipient:
        contact = frappe.db.get_value("Contact", {"email_id": recipient}, "name")
    else:
        contact = frappe.db.get_value("Contact", {"mobile_no": recipient}, "name")
    
    if contact:
        lang = frappe.get_cached_value("Contact", contact, "language")
        if lang:
            return lang
    
    # 3. Fallback to site default
    return frappe.db.get_default("language") or "en"
```

**B. Variant Rendering Function**
Add to `dispatcher.py`:
```python
def _render_template_variant(template_name: str, language: str, context: dict) -> dict:
    """Fetch and render the correct language variant."""
    # Try requested language, fallback to English, then first available
    variant = frappe.db.get_value(
        "Message Template Variant",
        {"parent": template_name, "language": language},
        ["body", "subject"], as_dict=True
    )
    
    if not variant:
        variant = frappe.db.get_value(
            "Message Template Variant",
            {"parent": template_name, "language": "en"},
            ["body", "subject"], as_dict=True
        )
    
    if not variant:
        variant = frappe.db.get_value(
            "Message Template Variant",
            {"parent": template_name},
            ["body", "subject"], order_by="creation", as_dict=True
        )
    
    return {
        "body": frappe.render_template(variant.body, context),
        "subject": frappe.render_template(variant.subject, context) if variant.subject else ""
    }
```

**C. Integrate into dispatch()**
```python
if message_type == "Template" and text:  # text contains template_name
    language = meta_template_language or _get_recipient_language(recipient, source_doctype, source_docname)
    context = frappe.get_doc(source_doctype, source_docname).as_dict() if source_docname else {}
    rendered = _render_template_variant(text, language, context)
    text = rendered["body"]
    subject = rendered.get("subject", "")
```

**Files to Modify:**
- [ ] `dispatcher.py` — add `_get_recipient_language()`, `_render_template_variant()`, integrate into `dispatch()`
- [ ] `Message Template` doctype — add `is_template_based` checkbox field
- [ ] Test with Hindi/Gujarati templates

---

### 3. Admin Workspace / Dashboard (LOW PRIORITY — Nice to Have)

**What:** Workspace with metrics and health monitoring.

**Implementation Needed:**

**A. New Workspace: `Notification Dashboard`**
```json
{
  "doctype": "Workspace",
  "name": "Notification Dashboard",
  "charts": [
    {"chart_name": "Sent Today", "source": "WhatsApp Send Log", "filters": {"status": "Sent", "date": "today"}},
    {"chart_name": "Failed Today", "source": "WhatsApp Send Log", "filters": {"status": "Failed", "date": "today"}},
    {"chart_name": "Circuit Breaker Status", "source": "Custom", "data": "kreativ_notification.notification.health.get_breaker_status"}
  ],
  "number_cards": [
    {"card_name": "Total Sent Today", "doctype": "WhatsApp Send Log", "field": "count", "filters": {"status": "Sent", "date": "today"}},
    {"card_name": "Total Failed", "doctype": "WhatsApp Send Log", "field": "count", "filters": {"status": "Failed", "date": "today"}},
    {"card_name": "Queued", "doctype": "WhatsApp Send Log", "field": "count", "filters": {"status": "Queued"}}
  ]
}
```

**B. Health Check API**
Add to `health.py`:
```python
@frappe.whitelist()
def get_dashboard_summary():
    return {
        "sent_today": frappe.db.count("WhatsApp Send Log", {"status": "Sent", "creation": [">=", frappe.utils.today()]}),
        "failed_today": frappe.db.count("WhatsApp Send Log", {"status": "Failed", "creation": [">=", frappe.utils.today()]}),
        "queued": frappe.db.count("WhatsApp Send Log", {"status": "Queued"}),
        "circuit_breakers": get_breaker_status()  # From existing health.py
    }
```

**Files to Create:**
- [ ] `kreativ_notification/kreativ_notification/notification/dashboard.py` — new workspace + charts
- [ ] Workspace JSON in `kreativ_notification/kreativ_notification/doctype/workspace/`

---

### 4. Bot Command Registry (LOW PRIORITY — Extensibility)

**What:** Allow other apps to register WhatsApp bot commands via hook.

**Current State:**
- ✅ Hook documented in `channels/__init__.py` and `hooks.py` (as comment)
- ❌ No actual registry implementation

**Implementation Needed:**

**A. Command Registry in `inbound.py`**
```python
# Global registry populated from hooks
BOT_COMMANDS = {}

def load_bot_commands():
    """Load commands from hooks at boot."""
    global BOT_COMMANDS
    BOT_COMMANDS = {
        "help": "kreativ_notification.notification.inbound.cmd_help",
        "stop": "kreativ_notification.notification.inbound.cmd_stop",
        "start": "kreativ_notification.notification.inbound.cmd_start",
    }
    # Load from other apps
    for hook_map in frappe.get_hooks("whatsapp_bot_commands") or []:
        if isinstance(hook_map, dict):
            BOT_COMMANDS.update(hook_map)

# In handle_inbound_message():
def handle_inbound_message(chat_id, message_text, ...):
    command = message_text.strip().lower().split()[0]
    if command in BOT_COMMANDS:
        handler = frappe.get_attr(BOT_COMMANDS[command])
        return handler(chat_id, message_text, ...)
    
    # ... existing fallback logic ...
```

**B. Example Commands**
```python
# In inbound.py
def cmd_stop(chat_id, message_text, ...):
    """Handle STOP command — opt out from notifications."""
    frappe.get_doc({
        "doctype": "Consent Registry",
        "contact": chat_id,
        "channel": get_channel_for_chat(chat_id),
        "status": "Opted Out",
        "opt_out_date": now_datetime(),
        "opt_out_reason": "User replied STOP"
    }).insert(ignore_permissions=True)
    
    send_text(chat_id, "You have been unsubscribed. Reply START to re-enable.")

def cmd_start(chat_id, message_text, ...):
    """Handle START command — opt in to notifications."""
    # Similar to cmd_stop but with status="Opted In"

def cmd_help(chat_id, message_text, ...):
    """Show available commands."""
    help_text = """
Available commands:
- STOP: Unsubscribe from notifications
- START: Re-subscribe to notifications
- INVOICE <ref>: Get invoice PDF
- LEDGER <customer>: Get ledger summary
    """
    send_text(chat_id, help_text)
```

**Files to Modify:**
- [ ] `inbound.py` — add command registry, load commands from hooks, implement cmd_stop/cmd_start/cmd_help
- [ ] `hooks.py` — uncomment and activate `whatsapp_bot_commands` hook registration

---

## 📊 Implementation Priority

| Enhancement | Priority | Effort | Legal Required | User Visible |
|-------------|----------|--------|----------------|--------------|
| **Consent Registry** | 🔴 HIGH | Medium | ✅ YES | Yes (STOP/START) |
| **Template Language Variants** | 🟡 MEDIUM | Low-Medium | No | Yes (localized messages) |
| **Admin Dashboard** | 🟢 LOW | Medium | No | Internal only |
| **Bot Command Registry** | 🟢 LOW | Low | No | Yes (new commands) |

**Recommended Order:**
1. **Consent Registry** — Do this first for legal compliance
2. **Template Language Variants** — Improves UX for multi-lingual customers
3. **Admin Dashboard** — Nice to have for monitoring
4. **Bot Command Registry** — Extensibility for other apps

---

## 📝 Migration Checklist (When Upgrading from v1 to v2)

- [ ] Backup database
- [ ] Copy platform files (diff `hooks.py` first!)
- [ ] `bench migrate`
- [ ] `bench --site <site> execute kreativ_notification.notification.setup_defaults.run`
- [ ] Verify Notification Channels created (Primary WhatsApp)
- [ ] Verify bootstrap rules (Salary Slip, Checkin) — enable if needed
- [ ] Delete old hardcoded hooks in `kreativ_attendance` and `gravures_custom`
- [ ] Test outbound sends (dashboard buttons, print preview)
- [ ] Test inbound bot (invoice lookup)
- [ ] **Implement Consent Registry** (if legal requirement applies)
- [ ] **Test language variants** (if multi-lingual customers)

---

## 🔗 References

- **Platform README:** `~/Desktop/PLATFORM_README.md`
- **Memory:** `~/.claude/projects/-home-mitesh/memory/fix-whitelist-not-registered-gunicorn.md`
- **GitHub Repos:**
  - https://github.com/mits1987/Kreativ_Notification (main)
  - https://github.com/mits1987/Gravures_Custom (main)
  - https://github.com/mits1987/Kreativ_Attendance (master)

---

## 📞 Support

For issues or questions:
1. Check logs: `tail -f ~/frappe-bench-v16/logs/web.log`
2. Check dispatcher logs: `tail -f ~/frappe-bench-v16/logs/kreativ_whitelist_check.log`
3. Verify channel health: `Notification Channel → Primary WhatsApp → Health Check`
4. Test API: `curl -X POST http://localhost:8316/api/method/kreativ_notification.api.send_test_whatsapp -H "Host: kreativ316"`