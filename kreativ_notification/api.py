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


_CSS = (
    "body{margin:0;padding:20px;font-family:Arial,sans-serif;background:#fff;}"
    "table{border-collapse:collapse;width:100%;}"
    "th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;font-size:12px;}"
    "th{background:#eef6f9;font-weight:bold;}"
    ".total-row{font-weight:bold;background:#eef6f9;}"
    ".highest-tmm{background:#d4edda;font-weight:bold;}"
    ".lowest-tmm{background:#f8d7da;font-weight:bold;}"
)


def _wrap_html(title, subtitle, body):
    return (
        '<!DOCTYPE html><html><head>'
        '<meta charset="utf-8">'
        '<link rel="stylesheet" href="/assets/frappe/css/frappe.css">'
        "<style>{css}</style></head><body>"
        "<h3 style='text-align:center;margin-bottom:1rem;'>{title}</h3>"
        "<p style='text-align:center;color:#666;font-size:13px;'>{sub}</p>"
        "{body}</body></html>"
    ).format(css=_CSS, title=title, sub=subtitle, body=body)


def _run_report_data(report_name, filters):
    result = frappe.call(
        "frappe.desk.query_report.run",
        report_name=report_name, filters=filters,
    )
    if not result:
        return [], []
    return result.get("result", []), result.get("columns", [])


def _fmt_tmm(val):
    try:
        return "{:.2f}".format(float(str(val).replace(",", "") or 0))
    except (ValueError, TypeError):
        return str(val or "")


def _highlight_cells(row, tmm_idx, max_tmm, min_tmm):
    if tmm_idx < 0 or tmm_idx >= len(row):
        return row
    try:
        v = float(str(row[tmm_idx]).replace(",", "") or 0)
    except (ValueError, TypeError):
        return row
    if max_tmm is not None and abs(v - max_tmm) < 0.01:
        return list(row[:tmm_idx]) + ['<td class="highest-tmm">{}</td>'.format(_fmt_tmm(v))] + list(row[tmm_idx+1:])
    if min_tmm is not None and abs(v - min_tmm) < 0.01:
        return list(row[:tmm_idx]) + ['<td class="lowest-tmm">{}</td>'.format(_fmt_tmm(v))] + list(row[tmm_idx+1:])
    return row


def _screenshot_and_send(html, filename):
    from kreativ_notification.notification.screenshot_utils import screenshot_html_playwright
    jpg = screenshot_html_playwright(html)
    b64 = base64.b64encode(jpg).decode("utf-8")
    return send_image_via_whatsapp(b64, f"{filename}.jpg", filename, source_docname="OpenWA Settings")


def _date_range_str(from_date, to_date):
    from gravures_custom.overrides import _format_date_range
    return _format_date_range(from_date, to_date)


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

    rows_html = ""
    for state in sorted(grouped.keys()):
        v = grouped[state]
        pct = "{:.1f}".format(v["tmm"] / total_tmm * 100) if total_tmm else "0.0"
        rows_html += "<tr><td>{}</td><td>{}</td><td>{:.2f}</td><td>{}%</td></tr>".format(
            state, v["count"], v["tmm"], pct)

    table = (
        '<table><thead><tr><th>Workflow State</th><th>Count</th><th>Total TMM</th><th>Percentage</th></tr></thead>'
        "<tbody>{}</tbody></table>".format(rows_html)
    )
    stats = "Total Sales Orders: {} | Total TMM: {:.2f}".format(total_count, total_tmm)
    html = _wrap_html("SALES ORDER STATUS", "{} | {}".format(_date_range_str(from_date, to_date), stats), table)
    return _screenshot_and_send(html, "job_status")


# 2. Dispatch by JobType (daily)
@frappe.whitelist()
def send_dispatch_whatsapp(from_date, to_date):
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

    main_rows = [[jt, str(v["cyl"]), _fmt_tmm(v["tmm"])] for jt, v in sorted(grouped.items()) if jt in main_types]
    excl_rows = [[jt, str(v["cyl"]), _fmt_tmm(v["tmm"])] for jt, v in sorted(grouped.items()) if jt not in main_types]

    def _build(rows, title):
        if not rows:
            return ""
        total_cyl = sum(int(r[1]) for r in rows)
        total_tmm = sum(float(r[2]) for r in rows)
        trs = "".join("<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(*r) for r in rows)
        trs += '<tr class="total-row"><td>Total</td><td>{}</td><td>{:.2f}</td></tr>'.format(total_cyl, total_tmm)
        return "<h4>{}</h4><table><thead><tr><th>Job Type</th><th>Total Cylinders</th><th>Total TMM</th></tr></thead><tbody>{}</tbody></table>".format(title, trs)

    parts = []
    if main_rows:
        parts.append(_build(main_rows, "Main Types"))
    if excl_rows:
        parts.append(_build(excl_rows, "Excluded Types"))
    body = '<div style="display:flex;gap:2rem;flex-wrap:wrap;">{}</div>'.format("".join('<div style="flex:1;">{}</div>'.format(p) for p in parts)) if len(parts) > 1 else (parts[0] if parts else "<p>No data</p>")
    html = _wrap_html("DAILY DISPATCH", _date_range_str(from_date, to_date), body)
    return _screenshot_and_send(html, "dispatch_jobtype")


# 3. Dispatch Monthly
@frappe.whitelist()
def send_dispatch_monthly_whatsapp(from_date, to_date):
    return send_dispatch_whatsapp(from_date, to_date)


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
                    cls = "highest-tmm" if max_tmm is not None and abs(fv - max_tmm) < 0.01 else ("lowest-tmm" if min_tmm is not None and abs(fv - min_tmm) < 0.01 else "")
                    cells.append('<td class="{}">{}</td>'.format(cls, _fmt_tmm(fv)))
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

    if cyl_idx >= 0 or tmm_idx >= 0:
        trs += '<tr class="total-row"><td>Total</td>'
        if cyl_idx >= 0:
            trs += "<td>{}</td>".format(total_cyl)
        if tmm_idx >= 0:
            trs += "<td>{:.2f}</td>".format(total_tmm)
        trs += "</tr>"

    ths = "".join("<th>{}</th>".format(c) for c in col_labels)
    table = "<table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>".format(ths, trs)
    html = _wrap_html("CUSTOMER DISPATCH SUMMARY", _date_range_str(from_date, to_date), table)
    return _screenshot_and_send(html, "dispatch_customer")


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

    main_rows = [[jt, str(v["cyl"]), _fmt_tmm(v["tmm"])] for jt, v in sorted(grouped.items()) if jt in main_types]
    excl_rows = [[jt, str(v["cyl"]), _fmt_tmm(v["tmm"])] for jt, v in sorted(grouped.items()) if jt not in main_types]

    def _build(rows, title):
        if not rows:
            return ""
        total_cyl = sum(int(r[1]) for r in rows)
        total_tmm = sum(float(r[2]) for r in rows)
        trs = "".join("<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(*r) for r in rows)
        trs += '<tr class="total-row"><td>Total</td><td>{}</td><td>{:.2f}</td></tr>'.format(total_cyl, total_tmm)
        return "<h4>{}</h4><table><thead><tr><th>Job Type</th><th>Total QTY</th><th>Total TMM</th></tr></thead><tbody>{}</tbody></table>".format(title, trs)

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
            trs += '<tr class="total-row"><td>Total</td><td>{}</td><td>{:.2f}</td></tr>'.format(m_cyl, m_tmm)
            monthly_html += "<h4>{} - {}</h4><table><thead><tr><th>Job Type</th><th>Total QTY</th><th>Total TMM</th></tr></thead><tbody>{}</tbody></table>".format(mk, label, trs)

    body = yearly_html + ("<hr>" + monthly_html if monthly_html else "")
    html = _wrap_html("DISPATCH YEARLY", _date_range_str(from_date, to_date), body)
    return _screenshot_and_send(html, "dispatch_yearly")


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
            for j, label in enumerate(desired):
                fname = field_map.get(label, label)
                v = row.get(fname, "") if isinstance(rows[0], dict) and hasattr(rows[0], 'get') else (vals[col_labels.index(label)] if label in col_labels and col_labels.index(label) < len(vals) else "")
                if label == "TMM":
                    try:
                        fv = float(str(v).replace(",", "") or 0)
                        machine_tmm += fv
                        v = "{:.2f}".format(fv)
                    except (ValueError, TypeError):
                        pass
                cells.append("<td>{}</td>".format(v))
            trs += "<tr>{}</tr>".format("".join(cells))
        trs += '<tr class="total-row"><td>Total</td>' + "<td></td>" * (len(desired) - 2) + "<td>{:.2f}</td></tr>".format(machine_tmm)
        ths = "".join("<th>{}</th>".format(c) for c in desired)
        body += "<h4>{}</h4><table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>".format(machine, ths, trs)

    html = _wrap_html("ENGRAVING DETAILS", _date_range_str(from_date, to_date), body or "<p>No data</p>")
    return _screenshot_and_send(html, "engraving_report")


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
    trs += '<tr class="total-row"><td>Total</td><td>{:.2f}</td></tr>'.format(grand_total)
    table = "<table><thead><tr><th>Machine</th><th>Total TMM</th></tr></thead><tbody>{}</tbody></table>".format(trs)
    html = _wrap_html("MONTHLY ENGRAVING SUMMARY", _date_range_str(from_date, to_date), table)
    return _screenshot_and_send(html, "engraving_summary")


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
        for j, label in enumerate(display_cols):
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

    total_cells = '<td>Total</td>' * len(display_cols)
    if qty_idx >= 0:
        total_cells = ""
        for i, c in enumerate(display_cols):
            if i == qty_idx:
                total_cells += "<td>{}</td>".format(total_qty)
            elif i == 0:
                total_cells += "<td>Total</td>"
            else:
                total_cells += "<td></td>"
    trs += '<tr class="total-row">{}</tr>'.format(total_cells)

    ths = "".join("<th>{}</th>".format(c) for c in display_cols)
    table = "<table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>".format(ths, trs)
    html = _wrap_html("PROOFING DAILY DETAILS", _date_range_str(from_date, to_date), table)
    return _screenshot_and_send(html, "proofing_report")


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
            cls = "highest-tmm" if abs(v["tmm"] - max_t) < 0.01 and max_t > 0 else ("lowest-tmm" if abs(v["tmm"] - min_t) < 0.01 and len(by_date) > 1 and min_t > 0 else "")
            trs += "<tr><td>{}</td><td>{}</td><td class=\"{}\">{:.2f}</td></tr>".format(d, v["qty"], cls, v["tmm"])
        trs += '<tr class="total-row"><td>Total</td><td>{}</td><td>{:.2f}</td></tr>'.format(t_qty, t_tmm)
        avg = t_tmm / len(by_date) if by_date else 0
        trs += '<tr style="background:#f9f9f9;"><td>Average</td><td></td><td>{:.2f}</td></tr>'.format(avg)
        body += "<h4>PROOFING</h4><table><thead><tr><th>Date</th><th>QTY</th><th>TMM</th></tr></thead><tbody>{}</tbody></table>".format(trs)

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
            max_d = max(by_date_machine[d].values()) if by_date_machine[d] else 0
            min_d = min(v for v in by_date_machine[d].values() if v > 0) if any(v > 0 for v in by_date_machine[d].values()) else 0
            cls = "highest-tmm" if abs(row_total - max(grand_total for grand_total in [sum(by_date_machine[dd].values()) for dd in by_date_machine])) < 0.01 else ""
            cells += '<td class="{}">{:.2f}</td>'.format(cls, row_total)
            trs += "<tr>{}</tr>".format(cells)
        total_cells = "<td>Total</td>"
        for m in sorted_machines:
            total_cells += "<td>{:.2f}</td>".format(sum(by_date_machine[d].get(m, 0) for d in by_date_machine))
        total_cells += "<td>{:.2f}</td></tr>".format(grand_total)
        trs += '<tr class="total-row">{}</tr>'.format(total_cells)
        avg = grand_total / len(by_date_machine) if by_date_machine else 0
        trs += '<tr style="background:#f9f9f9;"><td>Average</td>' + "<td></td>" * len(sorted_machines) + "<td>{:.2f}</td></tr>".format(avg)
        ths = "<th>Date</th>" + "".join("<th>{}</th>".format(m) for m in sorted_machines) + "<th>Total TMM</th>"
        body += "<h4>ENGRAVING</h4><table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>".format(ths, trs)

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
        trs += '<tr class="total-row">{}</tr>'.format(total_cells)
        ths = "<th>Date</th>"
        for jt in sorted_jts:
            ths += "<th>{} QTY</th><th>{} TMM</th>".format(jt, jt)
        ths += "<th>Total QTY</th><th>Total TMM</th>"
        body += "<h4>DISPATCH</h4><table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>".format(ths, trs)

    if not body:
        body = "<p>No data</p>"

    html = _wrap_html("MONTHLY SUMMARY - {}".format(period), _date_range_str(from_date, to_date), body)
    return _screenshot_and_send(html, "monthly_summary")


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