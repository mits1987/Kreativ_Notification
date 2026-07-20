"""Bootstrap default channel, templates, and rules.

Run once after migrating:

    bench --site yoursite execute kreativ_notification.notification.setup_defaults.run

Creates (idempotently):
    - "Primary WhatsApp" channel (OpenWA, credentials copied from legacy
      OpenWA Settings if present)
    - Message Templates + Notification Rules that replace the old
      hardcoded Salary Slip and Employee Checkin Python hooks
"""

import frappe


def run():
    channel = _ensure_channel()
    _ensure_salary_slip(channel)
    _ensure_checkin(channel)
    frappe.db.commit()
    print("Defaults created. Review them under Notification Rule / Message Template.")


def _ensure_channel() -> str:
    name = "Primary WhatsApp"
    if frappe.db.exists("Notification Channel", name):
        return name

    doc = frappe.get_doc({
        "doctype": "Notification Channel",
        "channel_name": name,
        "channel_type": "WhatsApp - OpenWA",
        "enabled": 1,
        "is_default": 1,
        "rate_limit_per_minute": 20,
    })

    # Copy legacy OpenWA Settings credentials if the single exists
    if frappe.db.exists("DocType", "OpenWA Settings"):
        legacy = frappe.get_cached_doc("OpenWA Settings")
        doc.base_url = legacy.base_url
        doc.session_id = legacy.session_id or "default"
        doc.default_country_code = getattr(legacy, "default_country_code", "91")
        api_key = legacy.get_password("api_key", raise_exception=False)
        if api_key:
            doc.api_key = api_key

    doc.insert(ignore_permissions=True)
    return name


def _ensure_template(name: str, for_doctype: str, body_en: str,
                     attach_print: bool = False, subject: str = "") -> str:
    if frappe.db.exists("Message Template", name):
        return name
    frappe.get_doc({
        "doctype": "Message Template",
        "template_name": name,
        "enabled": 1,
        "for_doctype": for_doctype,
        "default_language": "en",
        "attach_print": 1 if attach_print else 0,
        "variants": [{"language": "en", "subject": subject, "body": body_en}],
    }).insert(ignore_permissions=True)
    return name


def _ensure_rule(name: str, **kwargs) -> str:
    if frappe.db.exists("Notification Rule", name):
        return name
    
    # Extract recipients from kwargs to handle separately
    recipients = kwargs.pop("recipients", [])
    
    doc = frappe.get_doc({
        "doctype": "Notification Rule",
        "rule_name": name,
        "enabled": kwargs.pop("enabled", 0),
        **kwargs,
    })
    doc.insert(ignore_permissions=True)
    
    # Add recipients after the rule is created
    if recipients:
        rule = frappe.get_doc("Notification Rule", name)
        for r in recipients:
            rule.append("recipients", r)
        rule.save(ignore_permissions=True)
    
    return name


def _ensure_salary_slip(channel: str):
    if not frappe.db.exists("DocType", "Salary Slip"):
        return
    tpl = _ensure_template(
        "Salary Slip WhatsApp",
        "Salary Slip",
        body_en=(
            "Dear {{ doc.employee_name }},\n\n"
            "Your salary slip for {{ doc.start_date }} to {{ doc.end_date }} "
            "is attached.\n\nNet Pay: {{ frappe.utils.fmt_money(doc.net_pay, currency=doc.currency) }}"
        ),
        attach_print=True,
        subject="Salary Slip {{ doc.name }}",
    )
    _ensure_rule(
        "Salary Slip on Submit",
        document_type="Salary Slip",
        event="Submit",
        message_template=tpl,
        channel=channel,
        priority="Normal",
        recipients=[{
            "recipient_type": "Linked Contact",
            "value": "employee",
            "contact_fieldname": "cell_number",
        }],
    )


def _ensure_checkin(channel: str):
    if not frappe.db.exists("DocType", "Employee Checkin"):
        return
    tpl = _ensure_template(
        "Checkin WhatsApp",
        "Employee Checkin",
        body_en=(
            "{{ doc.employee_name }} — {{ doc.log_type }} at "
            "{{ frappe.utils.format_datetime(doc.time, 'HH:mm') }}"
        ),
    )
    _ensure_rule(
        "Checkin Notification",
        document_type="Employee Checkin",
        event="New",
        message_template=tpl,
        channel=channel,
        priority="Normal",
        recipients=[{
            "recipient_type": "Linked Contact",
            "value": "employee",
            "contact_fieldname": "cell_number",
        }],
    )
