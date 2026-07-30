import frappe
import re

def test_ledger_regex():
    site = "kreativ316"
    frappe.init(site=site)
    frappe.connect()
    
    message_text = "ledger kanck"
    settings = frappe.get_cached_doc("OpenWA Settings")

    for cmd in settings.bot_commands:
        if not cmd.enabled:
            continue
        keywords = [k.strip().lower() for k in cmd.command_keyword.split(",") if k.strip()]
        keyword_pattern = "|".join(re.escape(k) for k in keywords)
        pattern = rf"(?i)({keyword_pattern})\s*[#:]*\s*(.+)"
        match = re.search(pattern, message_text)
        if match:
            print(f"MATCH: {cmd.command_keyword} -> keyword={match.group(1)}, identifier={match.group(2)}")
        else:
            print(f"NO MATCH: {cmd.command_keyword}")
    
    frappe.destroy()

if __name__ == "__main__":
    test_ledger_regex()
