"""Public Whitelisted API for WhatsApp sending.

These replace the gravures_custom.overrides whitelisted functions.
All calls are delegated to kreativ_notification.notification modules.
"""

import base64

import frappe
from frappe import _

from kreativ_notification.notification.dashboard_senders import (
    send_custom_report,
)
from kreativ_notification.notification.send import (
    send_document_via_whatsapp,
    send_image_via_whatsapp,
    send_text_via_whatsapp,
    send_test_message,
)


def _get_openwa_settings():
    """Get OpenWA Settings document."""
    return frappe.get_cached_doc("OpenWA Settings")


def _get_openwa_client():
    """Create OpenWAClient with current settings."""
    settings = _get_openwa_settings()
    return __import__(
        "kreativ_notification.notification.openwa_client",
        fromlist=["OpenWAClient"]
    ).OpenWAClient(settings.base_url, settings.get_password("api_key"))


# --- Dashboard / Workspace send functions ---


@frappe.whitelist()
def send_screenshot_whatsapp(html_content: str, filename: str = "report") -> dict:
    """Send a browser-rendered screenshot via WhatsApp.

    Called by Custom HTML Block WhatsApp buttons that capture the
    rendered DOM and pass it here for screenshotting + dispatch.
    """
    from kreativ_notification.notification.screenshot_utils import screenshot_html_playwright
    jpg = screenshot_html_playwright(html_content)
    b64 = base64.b64encode(jpg).decode("utf-8")
    return send_image_via_whatsapp(
        b64,
        f"{filename}.jpg",
        filename,
        source_docname="OpenWA Settings",
    )


# --- Server-side report screenshot WhatsApp senders ---

_TBLS = "table table-bordered table-sm"
_HILITE = "highest-tmm"
_LOLITE = "lowest-tmm"
_TROW = "font-weight:bold;background:#eef6f9;"

_CSS = (
    "body{font-family:Arial,sans-serif;padding:16px;}"
    "table{border-collapse:collapse;width:100%;}"
    "th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;font-size:12px;}"
    "th{background:#eef6f9;font-weight:bold;}"
    ".highest-tmm{background:#d4edda;font-weight:bold;}"
    ".lowest-tmm{background:#f8d7da;font-weight:bold;}"
)


def _wrap_html(title, subtitle, body):
    return (
        '<html><head>'
        '<link rel="stylesheet" href="/assets/frappe/css/frappe.css">'
        '<style>' + _CSS + '</style></head><body>'
        '<h3 style="text-align:center;margin-bottom:1rem;">' + title + '</h3>'
        + ('<p style="text-align:center;color:#666;font-size:13px;margin:0 0 10px 0;">' + subtitle + '</p>' if subtitle else '')
        + body + '</body></html>'
    )


def _table(headers, rows, total_row=None):
    ths = ''.join('<th>' + c + '</th>' for c in headers)
    trs = ''
    for row in rows:
        trs += '<tr>' + ''.join(str(c) for c in row) + '</tr>'
    if total_row:
        trs += '<tr style="' + _TROW + '">' + ''.join(str(c) for c in total_row) + '</tr>'
    return '<table class="' + _TBLS + '"><thead><tr>' + ths + '</tr></thead><tbody>' + trs + '</tbody></table>'


def _run_report_data(report_name, filters):
    result = frappe.call(
        "frappe.desk.query_report.run",
        report_name=report_name, filters=filters,
    )
    if not result:
        return [], []
    return result.get("result", []), result.get("columns", [])


def _fmt(val):
    try:
        return "{:.2f}".format(float(str(val).replace(",", "") or 0))
    except (ValueError, TypeError):
        return str(val or "")


def _screenshot_and_send(html, filename, caption=""):
    from kreativ_notification.notification.screenshot_utils import screenshot_html_playwright
    jpg = screenshot_html_playwright(html)
    b64 = base64.b64encode(jpg).decode("utf-8")
    return send_image_via_whatsapp(b64, f"{filename}.jpg", caption or filename, source_docname="OpenWA Settings")


def _date_range_str(from_date, to_date):
    from gravures_custom.overrides import _format_date_range
    return _format_date_range(from_date, to_date)


def _cell(v):
    return '<td>' + str(v) + '</td>'


def _hcell(v):
    return '<th>' + str(v) + '</th>'


# 1. Job Status
@frappe.whitelist()
def send_job_status_whatsapp(from_date, to_date):
    EXCLUDED_STATE = "Ready for Billing"
    filters = [
        ["Sales Order", "docstatus", "=", 1],
        ["Sales Order", "workflow_state", "!=", EXCLUDED_STATE],
    ]
    if from_date:
        filters.append(["Sales Order", "transaction_date", ">=", from_date])
    if to_date:
        filters.append(["Sales Order", "transaction_date", "<=", to_date])

    data = frappe.get_list(
        "Sales Order",
        fields=["workflow_state", "custom_cylinder_tmm"],
        filters=filters,
        limit_page_length=10000,
    )
    grouped = {}
    for row in data:
        state = row.get("workflow_state") or "Unknown"
        tmm = float(str(row.get("custom_cylinder_tmm") or 0).replace(",", "") or 0)
        grouped.setdefault(state, {"count": 0, "tmm": 0.0})
        grouped[state]["count"] += 1
        grouped[state]["tmm"] += tmm

    total_count = sum(v["count"] for v in grouped.values())
    total_tmm = sum(v["tmm"] for v in grouped.values())

    # Text caption
    lines = ["📊 Sales Order Status - {}".format(_date_range_str(from_date, to_date))]
    for state in sorted(grouped.keys()):
        v = grouped[state]
        pct = "{:.1f}".format(v["tmm"] / total_tmm * 100) if total_tmm else "0.0"
        lines.append("• {}: {} orders | {} TMM ({}%)".format(state, v["count"], _fmt(v["tmm"]), pct))
    lines.append("")
    lines.append("Total: {} orders | {} TMM".format(total_count, _fmt(total_tmm)))
    caption = "\n".join(lines)

    rows_html = ""
    for state in sorted(grouped.keys()):
        v = grouped[state]
        pct = "{:.1f}".format(v["tmm"] / total_tmm * 100) if total_tmm else "0.0"
        rows_html += "<tr><td>{}</td><td>{}</td><td>{:.2f}</td><td>{}%</td></tr>".format(
            state, v["count"], v["tmm"], pct)

    table = _table(["Workflow State", "Count", "Total TMM", "Percentage"], [])
    table = table.replace("</tbody>", rows_html + "</tbody>")
    stats = "Total Sales Orders: {} | Total TMM: {:.2f}".format(total_count, total_tmm)
    html = _wrap_html("SALES ORDER STATUS", "{} | {}".format(_date_range_str(from_date, to_date), stats), table)
    return _screenshot_and_send(html, "job_status", caption)


# 2. Dispatch by JobType (daily)
@frappe.whitelist()
def send_dispatch_whatsapp(from_date, to_date, title="DAILY DISPATCH"):
    result, columns = _run_report_data("Dispatch By Custom_Closed", {"from_date": from_date, "to_date": to_date})
    main_types = ["CFAB", "FABG", "SUPG", "CSUP", "DCRC"]
    grouped = {}
    for row in result:
        if isinstance(row, dict):
            jt = (row.get("job_type") or "").strip()
            cyl = int(float(str(row.get("cylinders") or row.get("total_cylinders") or 0).replace(",", "") or 0))
            tmm = float(str(row.get("total_tmm") or row.get("tmm") or 0).replace(",", "") or 0)
        else:
            continue
        if not jt:
            continue
        grouped.setdefault(jt, {"cyl": 0, "tmm": 0.0})
        grouped[jt]["cyl"] += cyl
        grouped[jt]["tmm"] += tmm

    main_rows = [(jt, str(v["cyl"]), _fmt(v["tmm"])) for jt, v in sorted(grouped.items()) if jt in main_types]
    excl_rows = [(jt, str(v["cyl"]), _fmt(v["tmm"])) for jt, v in sorted(grouped.items()) if jt not in main_types]

    # Text caption
    lines = ["📊 {} - {}".format(title, _date_range_str(from_date, to_date))]
    if main_rows:
        lines.append("")
        lines.append("Main Job Types:")
        for jt, cyl, tmm in main_rows:
            lines.append("• {}: {} cyl | {} TMM".format(jt, cyl, tmm))
        m_cyl = sum(int(r[1]) for r in main_rows)
        m_tmm = sum(float(r[2]) for r in main_rows)
        lines.append("Total: {} cyl | {} TMM".format(m_cyl, _fmt(m_tmm)))
    if excl_rows:
        lines.append("")
        lines.append("Excluded:")
        for jt, cyl, tmm in excl_rows:
            lines.append("• {}: {} cyl | {} TMM".format(jt, cyl, tmm))
        e_cyl = sum(int(r[1]) for r in excl_rows)
        e_tmm = sum(float(r[2]) for r in excl_rows)
        lines.append("Total: {} cyl | {} TMM".format(e_cyl, _fmt(e_tmm)))
    caption = "\n".join(lines)

    def _build(rows, title):
        if not rows:
            return ""
        total_cyl = sum(int(r[1]) for r in rows)
        total_tmm = sum(float(r[2]) for r in rows)
        trs = "".join("<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(*r) for r in rows)
        trs += '<tr style="{}"><td>Total</td><td>{}</td><td>{:.2f}</td></tr>'.format(_TROW, total_cyl, total_tmm)
        return "<h4>{}</h4>".format(title) + _table(["Job Type", "Total Cylinders", "Total TMM"], []).replace("</tbody>", trs + "</tbody>")

    parts = []
    if main_rows:
        parts.append(_build(main_rows, "Main Types"))
    if excl_rows:
        parts.append(_build(excl_rows, "Excluded Types"))
    body = '<div style="display:flex;gap:2rem;flex-wrap:wrap;">{}</div>'.format("".join('<div style="flex:1;">{}</div>'.format(p) for p in parts)) if len(parts) > 1 else (parts[0] if parts else "<p>No data</p>")
    html = _wrap_html(title, _date_range_str(from_date, to_date), body)
    return _screenshot_and_send(html, "dispatch_jobtype", caption)


# 3. Dispatch Monthly
@frappe.whitelist()
def send_dispatch_monthly_whatsapp(from_date, to_date):
    return send_dispatch_whatsapp(from_date, to_date, title="MONTHLY DISPATCH")


# 4. Dispatch by Customer
@frappe.whitelist()
def send_dispatch_customer_whatsapp(from_date, to_date):
    result, columns = _run_report_data("Dispatch Monthly Customer", {"from_date": from_date, "to_date": to_date})
    if not result:
        return _screenshot_and_send(_wrap_html("CUSTOMER DISPATCH", _date_range_str(from_date, to_date), "<p>No data</p>"), "dispatch_customer")

    col_labels = [c.get("label", c.get("fieldname", "")) if isinstance(c, dict) else str(c) for c in columns]
    tmm_idx = next((i for i, c in enumerate(col_labels) if "tmm" in str(c).lower()), -1)
    cyl_idx = next((i for i, c in enumerate(col_labels) if "cylind" in str(c).lower()), -1)

    tmm_vals = []
    for row in result:
        if isinstance(row, dict) and tmm_idx >= 0:
            try:
                tmm_vals.append(float(str(list(row.values())[tmm_idx] if tmm_idx < len(row) else row.get("total_tmm", 0)).replace(",", "") or 0))
            except (ValueError, TypeError):
                pass
    max_tmm = max(tmm_vals) if tmm_vals else None
    min_tmm = min(tmm_vals) if tmm_vals else None

    trs = ""
    total_cyl = 0
    total_tmm = 0.0
    for row in result:
        if isinstance(row, dict):
            vals = list(row.values())
        elif isinstance(row, (list, tuple)):
            vals = list(row)
        else:
            continue
        cells = []
        for i, v in enumerate(vals):
            if i == tmm_idx:
                try:
                    fv = float(str(v).replace(",", "") or 0)
                    total_tmm += fv
                    cls = _HILITE if max_tmm is not None and abs(fv - max_tmm) < 0.01 else (_LOLITE if min_tmm is not None and abs(fv - min_tmm) < 0.01 else "")
                    cells.append('<td class="{}">{}</td>'.format(cls, _fmt(fv)))
                except (ValueError, TypeError):
                    cells.append("<td>{}</td>".format(v))
            elif i == cyl_idx:
                try:
                    total_cyl += int(float(str(v).replace(",", "") or 0))
                except (ValueError, TypeError):
                    pass
                cells.append("<td>{}</td>".format(v))
            else:
                cells.append("<td>{}</td>".format(v))
        trs += "<tr>{}</tr>".format("".join(cells))

    total_tr = '<tr style="{}"><td>Total</td>'.format(_TROW)
    if cyl_idx >= 0:
        total_tr += "<td>{}</td>".format(total_cyl)
    if tmm_idx >= 0:
        total_tr += "<td>{:.2f}</td>".format(total_tmm)
    total_tr += "</tr>"
    trs += total_tr

    tbl = _table(col_labels, []).replace("</tbody>", trs + "</tbody>")
    html = _wrap_html("CUSTOMER DISPATCH SUMMARY", _date_range_str(from_date, to_date), tbl)

    # Text caption
    lines = ["📊 Customer Dispatch Summary - {}".format(_date_range_str(from_date, to_date))]
    for row in result:
        if isinstance(row, dict):
            vals = list(row.values())
        elif isinstance(row, (list, tuple)):
            vals = list(row)
        else:
            continue
        parts = []
        for i, v in enumerate(vals):
            if i < len(col_labels):
                parts.append("{}: {}".format(col_labels[i], v))
        lines.append("• " + " | ".join(parts))
    lines.append("")
    lines.append("Total: {} cyl | {} TMM".format(total_cyl, _fmt(total_tmm)))
    caption = "\n".join(lines)

    return _screenshot_and_send(html, "dispatch_customer", caption)


# 5. Dispatch Yearly
@frappe.whitelist()
def send_dispatch_yearly_whatsapp(from_date, to_date):
    result, columns = _run_report_data("Dispatch By Custom_Closed", {"from_date": from_date, "to_date": to_date})
    main_types = ["CFAB", "FABG", "SUPG", "CSUP", "DCRC"]
    grouped = {}
    monthly = {}
    for row in result:
        if not isinstance(row, dict):
            continue
        jt = (row.get("job_type") or "").strip()
        cyl = int(float(str(row.get("cylinders") or row.get("total_cylinders") or 0).replace(",", "") or 0))
        tmm = float(str(row.get("total_tmm") or row.get("tmm") or 0).replace(",", "") or 0)
        posting = str(row.get("posting_date") or "")
        if not jt:
            continue
        grouped.setdefault(jt, {"cyl": 0, "tmm": 0.0})
        grouped[jt]["cyl"] += cyl
        grouped[jt]["tmm"] += tmm
        month_key = posting[:7] if posting else "Unknown"
        monthly.setdefault(month_key, {})
        monthly[month_key].setdefault(jt, {"cyl": 0, "tmm": 0.0})
        monthly[month_key][jt]["cyl"] += cyl
        monthly[month_key][jt]["tmm"] += tmm

    main_rows = [(jt, str(v["cyl"]), _fmt(v["tmm"])) for jt, v in sorted(grouped.items()) if jt in main_types]
    excl_rows = [(jt, str(v["cyl"]), _fmt(v["tmm"])) for jt, v in sorted(grouped.items()) if jt not in main_types]

    def _build(rows, title):
        if not rows:
            return ""
        total_cyl = sum(int(r[1]) for r in rows)
        total_tmm = sum(float(r[2]) for r in rows)
        trs = "".join("<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(*r) for r in rows)
        trs += '<tr style="{}"><td>Total</td><td>{}</td><td>{:.2f}</td></tr>'.format(_TROW, total_cyl, total_tmm)
        return "<h4>{}</h4>".format(title) + _table(["Job Type", "Total QTY", "Total TMM"], []).replace("</tbody>", trs + "</tbody>")

    yearly_parts = []
    if main_rows:
        yearly_parts.append(_build(main_rows, "Main Types"))
    if excl_rows:
        yearly_parts.append(_build(excl_rows, "Excluded Types"))
    yearly_html = '<div style="display:flex;gap:2rem;flex-wrap:wrap;">{}</div>'.format("".join('<div style="flex:1;">{}</div>'.format(p) for p in yearly_parts)) if len(yearly_parts) > 1 else (yearly_parts[0] if yearly_parts else "<p>No data</p>")

    monthly_html = ""
    for mk in sorted(monthly.keys()):
        mdata = monthly[mk]
        m_main = [(jt, v) for jt, v in sorted(mdata.items()) if jt in main_types]
        m_excl = [(jt, v) for jt, v in sorted(mdata.items()) if jt not in main_types]
        for label, mrows in [("Main Types", m_main), ("Excluded Types", m_excl)]:
            if not mrows:
                continue
            m_cyl = sum(v["cyl"] for _, v in mrows)
            m_tmm = sum(v["tmm"] for _, v in mrows)
            trs = "".join("<tr><td>{}</td><td>{}</td><td>{:.2f}</td></tr>".format(jt, v["cyl"], v["tmm"]) for jt, v in mrows)
            trs += '<tr style="{}"><td>Total</td><td>{}</td><td>{:.2f}</td></tr>'.format(_TROW, m_cyl, m_tmm)
            monthly_html += "<h4>{} - {}</h4>".format(mk, label) + _table(["Job Type", "Total QTY", "Total TMM"], []).replace("</tbody>", trs + "</tbody>")

    body = yearly_html + ("<hr>" + monthly_html if monthly_html else "")
    html = _wrap_html("DISPATCH YEARLY", _date_range_str(from_date, to_date), body)

    # Text caption
    lines = ["📊 Dispatch Yearly - {}".format(_date_range_str(from_date, to_date))]
    if main_rows:
        lines.append("")
        lines.append("Main Job Types:")
        for jt, cyl, tmm in main_rows:
            lines.append("• {}: {} cyl | {} TMM".format(jt, cyl, tmm))
        m_cyl = sum(int(r[1]) for r in main_rows)
        m_tmm = sum(float(r[2]) for r in main_rows)
        lines.append("Total: {} cyl | {} TMM".format(m_cyl, _fmt(m_tmm)))
    if excl_rows:
        lines.append("")
        lines.append("Excluded:")
        for jt, cyl, tmm in excl_rows:
            lines.append("• {}: {} cyl | {} TMM".format(jt, cyl, tmm))
    caption = "\n".join(lines)

    return _screenshot_and_send(html, "dispatch_yearly", caption)


# 6. Engraving (daily)
@frappe.whitelist()
def send_engraving_whatsapp(from_date, to_date):
    result, columns = _run_report_data("Engraving Details By Date", {"from_date": from_date, "to_date": to_date})
    if not result:
        return _screenshot_and_send(_wrap_html("ENGRAVING DETAILS", _date_range_str(from_date, to_date), "<p>No data</p>"), "engraving_report")

    col_labels = [c.get("label", c.get("fieldname", "")) if isinstance(c, dict) else str(c) for c in columns]
    desired = ["Sales Order", "Customer", "Job Name", "TMM", "Color", "Circum", "Length",
               "Stylus", "Screen/Angel", "Shadow", "Highlight", "Channel", "Remarks"]
    field_map = {}
    for c in columns:
        label = c.get("label", "") if isinstance(c, dict) else str(c)
        fname = c.get("fieldname", label) if isinstance(c, dict) else str(c)
        field_map[label] = fname

    machine_idx = next((i for i, c in enumerate(col_labels) if c == "Machine"), -1)
    tmm_idx = next((i for i, c in enumerate(col_labels) if "tmm" in str(c).lower()), -1)

    by_machine = {}
    for row in result:
        if isinstance(row, dict):
            vals = list(row.values())
        elif isinstance(row, (list, tuple)):
            vals = list(row)
        else:
            continue
        machine = str(vals[machine_idx]) if machine_idx >= 0 and machine_idx < len(vals) else "Default"
        by_machine.setdefault(machine, []).append(vals)

    body = ""
    for machine in sorted(by_machine.keys()):
        rows = by_machine[machine]
        trs = ""
        machine_tmm = 0.0
        for vals in rows:
            cells = []
            for label in desired:
                if label in col_labels:
                    idx = col_labels.index(label)
                    v = vals[idx] if idx < len(vals) else ""
                else:
                    v = ""
                if label == "TMM":
                    try:
                        fv = float(str(v).replace(",", "") or 0)
                        machine_tmm += fv
                        v = "{:.2f}".format(fv)
                    except (ValueError, TypeError):
                        pass
                cells.append("<td>{}</td>".format(v))
            trs += "<tr>{}</tr>".format("".join(cells))
        trs += '<tr style="{}"><td>Total</td>'.format(_TROW) + "<td></td>" * (len(desired) - 2) + "<td>{:.2f}</td></tr>".format(machine_tmm)
        body += "<h4>{}</h4>".format(machine) + _table(desired, []).replace("</tbody>", trs + "</tbody>")

    html = _wrap_html("ENGRAVING DETAILS", _date_range_str(from_date, to_date), body or "<p>No data</p>")

    # Text caption
    lines = ["📊 Engraving Details - {}".format(_date_range_str(from_date, to_date))]
    grand_tmm = 0.0
    for machine in sorted(by_machine.keys()):
        rows = by_machine[machine]
        machine_tmm = 0.0
        for vals in rows:
            if tmm_idx >= 0 and tmm_idx < len(vals):
                try:
                    machine_tmm += float(str(vals[tmm_idx]).replace(",", "") or 0)
                except (ValueError, TypeError):
                    pass
        grand_tmm += machine_tmm
        lines.append("")
        lines.append("{}: {} jobs | {} TMM".format(machine, len(rows), _fmt(machine_tmm)))
    lines.append("")
    lines.append("Grand Total: {} TMM".format(_fmt(grand_tmm)))
    caption = "\n".join(lines)

    return _screenshot_and_send(html, "engraving_report", caption)


# 7. Engraving Monthly
@frappe.whitelist()
def send_engraving_monthly_whatsapp(from_date, to_date):
    result, columns = _run_report_data("Engraving Details By Date", {"from_date": from_date, "to_date": to_date})
    if not result:
        return _screenshot_and_send(_wrap_html("MONTHLY ENGRAVING SUMMARY", _date_range_str(from_date, to_date), "<p>No data</p>"), "engraving_summary")

    col_labels = [c.get("label", c.get("fieldname", "")) if isinstance(c, dict) else str(c) for c in columns]
    machine_idx = next((i for i, c in enumerate(col_labels) if c == "Machine"), -1)
    tmm_idx = next((i for i, c in enumerate(col_labels) if "tmm" in str(c).lower()), -1)

    machine_totals = {}
    for row in result:
        vals = list(row.values()) if isinstance(row, dict) else list(row) if isinstance(row, (list, tuple)) else []
        machine = str(vals[machine_idx]) if machine_idx >= 0 and machine_idx < len(vals) else "Default"
        try:
            tmm = float(str(vals[tmm_idx]).replace(",", "") or 0) if tmm_idx >= 0 and tmm_idx < len(vals) else 0
        except (ValueError, TypeError):
            tmm = 0
        machine_totals[machine] = machine_totals.get(machine, 0.0) + tmm

    grand_total = sum(machine_totals.values())
    trs = "".join("<tr><td>{}</td><td>{:.2f}</td></tr>".format(m, v) for m, v in sorted(machine_totals.items()))
    trs += '<tr style="{}"><td>Total</td><td>{:.2f}</td></tr>'.format(_TROW, grand_total)
    tbl = _table(["Machine", "Total TMM"], []).replace("</tbody>", trs + "</tbody>")
    html = _wrap_html("MONTHLY ENGRAVING SUMMARY", _date_range_str(from_date, to_date), tbl)

    # Text caption
    lines = ["📊 Engraving Monthly Summary - {}".format(_date_range_str(from_date, to_date))]
    for m, v in sorted(machine_totals.items()):
        lines.append("• {}: {} TMM".format(m, _fmt(v)))
    lines.append("")
    lines.append("Total: {} TMM".format(_fmt(grand_total)))
    caption = "\n".join(lines)

    return _screenshot_and_send(html, "engraving_summary", caption)


# 8. Proofing
@frappe.whitelist()
def send_proofing_whatsapp(from_date, to_date):
    result, columns = _run_report_data("Proofing Details By Date", {"from_date": from_date, "to_date": to_date})
    if not result:
        return _screenshot_and_send(_wrap_html("PROOFING DAILY DETAILS", _date_range_str(from_date, to_date), "<p>No data</p>"), "proofing_report")

    col_labels = [c.get("label", c.get("fieldname", "")) if isinstance(c, dict) else str(c) for c in columns]
    display_cols = [c for c in col_labels if c != "Date"]
    rename = {"No of Cylinders": "QTY", "Final Height": "Circum", "Full Width": "Length"}
    display_cols = [rename.get(c, c) for c in display_cols]

    field_map = {}
    for c in columns:
        label = c.get("label", "") if isinstance(c, dict) else str(c)
        fname = c.get("fieldname", label) if isinstance(c, dict) else str(c)
        field_map[label] = fname

    qty_idx = next((i for i, c in enumerate(display_cols) if c == "QTY"), -1)
    total_qty = 0

    trs = ""
    for row in result:
        vals = list(row.values()) if isinstance(row, dict) else list(row) if isinstance(row, (list, tuple)) else []
        cells = []
        for label in display_cols:
            orig_label = [k for k, v in rename.items() if v == label]
            orig = orig_label[0] if orig_label else label
            fname = field_map.get(orig, orig)
            v = row.get(fname, "") if isinstance(row, dict) else (vals[col_labels.index(orig)] if orig in col_labels and col_labels.index(orig) < len(vals) else "")
            if label == "QTY":
                try:
                    total_qty += int(float(str(v).replace(",", "") or 0))
                except (ValueError, TypeError):
                    pass
            cells.append("<td>{}</td>".format(v))
        trs += "<tr>{}</tr>".format("".join(cells))

    total_cells = ""
    for i, c in enumerate(display_cols):
        if i == qty_idx:
            total_cells += "<td>{}</td>".format(total_qty)
        elif i == 0:
            total_cells += "<td>Total</td>"
        else:
            total_cells += "<td></td>"
    trs += '<tr style="{}">{}</tr>'.format(_TROW, total_cells)

    tbl = _table(display_cols, []).replace("</tbody>", trs + "</tbody>")
    html = _wrap_html("PROOFING DAILY DETAILS", _date_range_str(from_date, to_date), tbl)

    # Text caption
    lines = ["📊 Proofing Details - {}".format(_date_range_str(from_date, to_date))]
    for row in result:
        if isinstance(row, dict):
            vals = list(row.values())
        elif isinstance(row, (list, tuple)):
            vals = list(row)
        else:
            continue
        parts = []
        for label in display_cols:
            orig_label = [k for k, v in rename.items() if v == label]
            orig = orig_label[0] if orig_label else label
            fname = field_map.get(orig, orig)
            v = row.get(fname, "") if isinstance(row, dict) else (vals[col_labels.index(orig)] if orig in col_labels and col_labels.index(orig) < len(vals) else "")
            if v:
                parts.append("{}: {}".format(label, v))
        if parts:
            lines.append("• " + " | ".join(parts))
    lines.append("")
    lines.append("Total QTY: {}".format(total_qty))
    caption = "\n".join(lines)

    return _screenshot_and_send(html, "proofing_report", caption)


# 9. Monthly Summary (proofing + engraving + dispatch)
@frappe.whitelist()
def send_monthly_report_whatsapp(month):
    from frappe.utils import get_first_day, get_last_day
    first = get_first_day(month + "-01")
    last = get_last_day(month + "-01")
    from_date = str(first)
    to_date = str(last)
    period = frappe.utils.format_date(first, "MMMM yyyy")

    proofing_result, _ = _run_report_data("Proofing Details By Date", {"from_date": from_date, "to_date": to_date})
    engraving_result, engraving_cols = _run_report_data("Engraving Details By Date", {"from_date": from_date, "to_date": to_date})
    dispatch_result, _ = _run_report_data("Dispatch By Custom_Closed", {"from_date": from_date, "to_date": to_date})

    body = ""

    if proofing_result:
        from collections import defaultdict
        by_date = defaultdict(lambda: {"qty": 0, "tmm": 0.0})
        for row in proofing_result:
            if isinstance(row, dict):
                d = str(row.get("posting_date") or row.get("date") or "")[:10]
                qty = int(float(str(row.get("no_of_cylinders") or row.get("cylinders") or 0).replace(",", "") or 0))
                tmm = float(str(row.get("tmm") or 0).replace(",", "") or 0)
                by_date[d]["qty"] += qty
                by_date[d]["tmm"] += tmm
        t_qty = sum(v["qty"] for v in by_date.values())
        t_tmm = sum(v["tmm"] for v in by_date.values())
        max_t = max((v["tmm"] for v in by_date.values()), default=0)
        min_t = min((v["tmm"] for v in by_date.values()), default=0)
        trs = ""
        for d in sorted(by_date.keys()):
            v = by_date[d]
            cls = _HILITE if abs(v["tmm"] - max_t) < 0.01 and max_t > 0 else (_LOLITE if abs(v["tmm"] - min_t) < 0.01 and len(by_date) > 1 and min_t > 0 else "")
            trs += "<tr><td>{}</td><td>{}</td><td class=\"{}\">{:.2f}</td></tr>".format(d, v["qty"], cls, v["tmm"])
        trs += '<tr style="{}"><td>Total</td><td>{}</td><td>{:.2f}</td></tr>'.format(_TROW, t_qty, t_tmm)
        avg = t_tmm / len(by_date) if by_date else 0
        trs += '<tr style="background:#f9f9f9;"><td>Average</td><td></td><td>{:.2f}</td></tr>'.format(avg)
        body += "<h4>PROOFING</h4>" + _table(["Date", "QTY", "TMM"], []).replace("</tbody>", trs + "</tbody>")

    if engraving_result:
        from collections import defaultdict
        by_date_machine = defaultdict(lambda: defaultdict(float))
        machines = set()
        for row in engraving_result:
            if isinstance(row, dict):
                d = str(row.get("posting_date") or row.get("date") or "")[:10]
                machine = str(row.get("machine") or "Default")
                tmm = float(str(row.get("tmm") or 0).replace(",", "") or 0)
                by_date_machine[d][machine] += tmm
                machines.add(machine)
        sorted_machines = sorted(machines)
        all_totals = [sum(by_date_machine[dd].values()) for dd in by_date_machine]
        max_row_total = max(all_totals) if all_totals else 0
        grand_total = 0
        trs = ""
        for d in sorted(by_date_machine.keys()):
            cells = "<td>{}</td>".format(d)
            row_total = 0
            for m in sorted_machines:
                v = by_date_machine[d].get(m, 0)
                row_total += v
                cells += "<td>{:.2f}</td>".format(v)
            grand_total += row_total
            cls = _HILITE if abs(row_total - max_row_total) < 0.01 and max_row_total > 0 else ""
            cells += '<td class="{}">{:.2f}</td>'.format(cls, row_total)
            trs += "<tr>{}</tr>".format(cells)
        total_cells = "<td>Total</td>"
        for m in sorted_machines:
            total_cells += "<td>{:.2f}</td>".format(sum(by_date_machine[d].get(m, 0) for d in by_date_machine))
        total_cells += "<td>{:.2f}</td>".format(grand_total)
        trs += '<tr style="{}">{}</tr>'.format(_TROW, total_cells)
        avg = grand_total / len(by_date_machine) if by_date_machine else 0
        trs += '<tr style="background:#f9f9f9;"><td>Average</td>' + "<td></td>" * len(sorted_machines) + "<td>{:.2f}</td></tr>".format(avg)
        ths = ["Date"] + sorted_machines + ["Total TMM"]
        body += "<h4>ENGRAVING</h4>" + _table(ths, []).replace("</tbody>", trs + "</tbody>")

    if dispatch_result:
        from collections import defaultdict
        by_date_jt = defaultdict(lambda: defaultdict(lambda: {"cyl": 0, "tmm": 0.0}))
        jts = set()
        for row in dispatch_result:
            if isinstance(row, dict):
                d = str(row.get("posting_date") or row.get("date") or "")[:10]
                jt = (row.get("job_type") or "").strip()
                cyl = int(float(str(row.get("cylinders") or row.get("total_cylinders") or 0).replace(",", "") or 0))
                tmm = float(str(row.get("total_tmm") or row.get("tmm") or 0).replace(",", "") or 0)
                by_date_jt[d][jt]["cyl"] += cyl
                by_date_jt[d][jt]["tmm"] += tmm
                if jt:
                    jts.add(jt)
        sorted_jts = sorted(jts)
        grand_cyl = 0
        grand_tmm = 0.0
        trs = ""
        for d in sorted(by_date_jt.keys()):
            cells = "<td>{}</td>".format(d)
            d_cyl = 0
            d_tmm = 0.0
            for jt in sorted_jts:
                v = by_date_jt[d].get(jt, {"cyl": 0, "tmm": 0.0})
                d_cyl += v["cyl"]
                d_tmm += v["tmm"]
                cells += "<td>{}</td><td>{:.2f}</td>".format(v["cyl"], v["tmm"])
            grand_cyl += d_cyl
            grand_tmm += d_tmm
            cells += "<td>{}</td><td>{:.2f}</td>".format(d_cyl, d_tmm)
            trs += "<tr>{}</tr>".format(cells)
        total_cells = "<td>Total</td>"
        for jt in sorted_jts:
            tc = sum(by_date_jt[d].get(jt, {"cyl": 0, "tmm": 0.0})["cyl"] for d in by_date_jt)
            tt = sum(by_date_jt[d].get(jt, {"cyl": 0, "tmm": 0.0})["tmm"] for d in by_date_jt)
            total_cells += "<td>{}</td><td>{:.2f}</td>".format(tc, tt)
        total_cells += "<td>{}</td><td>{:.2f}</td>".format(grand_cyl, grand_tmm)
        trs += '<tr style="{}">{}</tr>'.format(_TROW, total_cells)
        ths = ["Date"]
        for jt in sorted_jts:
            ths += ["{} QTY".format(jt), "{} TMM".format(jt)]
        ths += ["Total QTY", "Total TMM"]
        body += "<h4>DISPATCH</h4>" + _table(ths, []).replace("</tbody>", trs + "</tbody>")

    if not body:
        body = "<p>No data</p>"

    html = _wrap_html("MONTHLY SUMMARY - {}".format(period), _date_range_str(from_date, to_date), body)

    # Text caption
    lines = ["📊 Monthly Summary - {}".format(period)]
    if proofing_result:
        t_qty = sum(v["qty"] for v in by_date.values()) if by_date else 0
        t_tmm = sum(v["tmm"] for v in by_date.values()) if by_date else 0
        lines.append("")
        lines.append("Proofing: {} QTY | {} TMM".format(t_qty, _fmt(t_tmm)))
    if engraving_result:
        lines.append("")
        lines.append("Engraving:")
        for m, v in sorted(machine_totals.items()):
            lines.append("• {}: {} TMM".format(m, _fmt(v)))
        lines.append("Total: {} TMM".format(_fmt(grand_total)))
    if dispatch_result:
        lines.append("")
        lines.append("Dispatch: {} cyl | {} TMM".format(grand_cyl, _fmt(grand_tmm)))
    caption = "\n".join(lines)

    return _screenshot_and_send(html, "monthly_summary", caption)


# --- Print Preview WhatsApp button helpers ---

@frappe.whitelist()
def validate_phone_number(phone: str) -> dict:
    """Validate and format phone number for WhatsApp."""
    try:
        import phonenumbers
        parsed = phonenumbers.parse(phone, "IN")
        if not phonenumbers.is_valid_number(parsed):
            return {"valid": False, "error": _("Invalid phone number")}
        formatted = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        return {"valid": True, "formatted": formatted}
    except Exception as e:
        return {"valid": False, "error": str(e)}


@frappe.whitelist()
def get_whatsapp_chats(limit: int = 50) -> dict:
    """Fetch recent WhatsApp chats from OpenWA for contact picker."""
    settings = _get_openwa_settings()
    if not settings.enabled:
        return {"chats": [], "groups": []}

    client = _get_openwa_client()

    try:
        resp = client.get_chats()
        # Client already returns normalized format; just return it
        return {"chats": resp.get("chats", []), "groups": resp.get("groups", [])}
    except Exception as e:
        frappe.log_error(f"Failed to fetch WhatsApp chats: {e}", "WhatsApp Chats")
        return {"chats": [], "groups": []}


@frappe.whitelist()
def search_whatsapp_contacts(query: str, limit: int = 20) -> dict:
    """Search WhatsApp contacts by name/number."""
    settings = _get_openwa_settings()
    if not settings.enabled:
        return {"chats": [], "groups": []}

    client = _get_openwa_client()

    try:
        resp = client.get_chats(search=query)
        return resp
    except Exception as e:
        frappe.log_error(f"WhatsApp contact search failed: {e}", "WhatsApp Search")
        return {"chats": [], "groups": []}


@frappe.whitelist()
def send_print_pdf_whatsapp(
    doctype: str,
    name: str = None,
    docname: str = None,
    print_format: str = None,
    chat_id: str = None,
    caption: str = None,
) -> dict:
    # Accept both 'name' (used in print preview JS) and 'docname' (legacy)
    doc_name = name or docname
    if not doc_name:
        frappe.throw(_("Missing parameter: name or docname"))
    """Generate PDF from print preview and send via WhatsApp."""
    from kreativ_notification.notification.pdf_utils import generate_pdf_bytes
    import base64

    # Use the print_format passed from print preview, fallback to "Standard"
    # Do NOT use deprecated invoice_print_format field
    resolved_print_format = print_format or "Standard"

    pdf_bytes = generate_pdf_bytes(doctype, doc_name, resolved_print_format, channel_name="WhatsApp - OpenWA")
    if isinstance(pdf_bytes, bytes):
        base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
    else:
        base64_pdf = pdf_bytes

    return send_document_via_whatsapp(
        base64_pdf=base64_pdf,
        filename=f"{doc_name}.pdf",
        caption=caption or f"{doctype}: {doc_name}",
        chat_id_override=chat_id,
        source_doctype=doctype,
        source_docname=doc_name,
        source_print_format=resolved_print_format,
    )


# --- Session management ---

@frappe.whitelist()
def get_openwa_session_status() -> dict:
    """Return current OpenWA session status for admin UI."""
    settings = _get_openwa_settings()
    return {
        "enabled": settings.enabled,
        "base_url": settings.base_url,
        "session_id": settings.session_id,
        "chat_id": settings.chat_id,
        "status": "ready" if settings.session_id else "not_configured",
    }


@frappe.whitelist()
def get_openwa_session_qr() -> dict:
    """Generate QR code for new OpenWA session."""
    settings = _get_openwa_settings()
    if not settings.enabled:
        return {"success": False, "error": "WhatsApp not enabled"}

    client = _get_openwa_client()

    try:
        qr_data = client.start_session()
        return {"success": True, "qr_code": qr_data.get("qr_code")}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def start_openwa_session() -> dict:
    """Start/resume OpenWA session."""
    return get_openwa_session_qr()


@frappe.whitelist()
def stop_openwa_session() -> dict:
    """Stop current OpenWA session."""
    settings = _get_openwa_settings()
    client = _get_openwa_client()
    try:
        client.stop_session()
        settings.session_id = ""
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def send_test_whatsapp() -> dict:
    """Send a test message to the admin chat."""
    return send_test_message()


@frappe.whitelist()
def get_customer_ledger_pdf(customer: str) -> dict:
    """Generate and return Customer Ledger PDF as base64 for remote fetch."""
    from kreativ_notification.notification.pdf_utils import generate_pdf_from_html
    from erpnext.accounts.report.general_ledger.general_ledger import execute as get_gl
    import base64

    # Get customer name (could be name or customer_name)
    customer_doc = frappe.get_cached_doc("Customer", customer)
    customer_name = customer_doc.name
    customer_display = customer_doc.customer_name

    company = frappe.db.get_single_value("Global Defaults", "default_company")
    company_doc = frappe.get_cached_doc("Company", company)
    company_currency = company_doc.default_currency

    filters = frappe._dict({
        "company": company,
        "from_date": frappe.utils.add_months(frappe.utils.today(), -12),
        "to_date": frappe.utils.today(),
        "party_type": "Customer",
        "party": [customer_name],
        "party_name": [customer_display],
        "show_remarks": 0,
        "categorize_by": "Categorize by Voucher (Consolidated)",
        "show_opening_entries": 1,
        "include_default_book_entries": 0,
    })

    columns, result = get_gl(filters)
    if not result:
        return {"success": False, "error": f"No ledger entries found for {customer_display}"}

    # Filter out only the Closing row from GL report (keep Total row)
    result = [r for r in result if not (r.get("account") == "'Closing (Opening + Total)'")]

    # Get default letterhead
    letter_head = frappe.get_cached_doc("Letter Head", {"is_default": 1}) if frappe.db.exists("Letter Head", {"is_default": 1}) else None

    html = frappe.render_template(
        "kreativ_notification/templates/customer_ledger.html",
        {
            "filters": filters,
            "data": result,
            "report": {"report_name": "General Ledger", "columns": columns},
            "ageing": None,
            "letter_head": letter_head,
            "terms_and_conditions": None,
            "company_name": company_doc.name,
            "company_address": company_doc.get("address", "") or "",
            "customer_name": customer_display,
            "customer": customer_name,
            "from_date": frappe.format(filters.from_date, "Date"),
            "to_date": frappe.format(filters.to_date, "Date"),
            "company_currency": company_currency,
        }
    )

    pdf_bytes = generate_pdf_from_html(html, channel_name="WhatsApp - OpenWA")
    base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

    return {"success": True, "pdf_base64": base64_pdf, "filename": f"Statement_{customer_name}.pdf"}