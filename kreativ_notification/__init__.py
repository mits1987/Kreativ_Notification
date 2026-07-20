__version__ = "0.0.1"

import sys
import os

# Ensure apps directory is in Python path for gunicorn --preload compatibility
APPS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
if APPS_PATH not in sys.path:
    sys.path.insert(0, APPS_PATH)

# =====================================================================
# CRITICAL: Import API at module load time so @frappe.whitelist()
# decorators execute during gunicorn --preload worker initialization.
# This populates frappe.whitelisted BEFORE any requests arrive.
# =====================================================================
from . import api  # noqa: F401


def ensure_api_loaded():
    """Hook called before every request to verify API module is loaded.

    This is a safety net - the module-level import above handles
    gunicorn --preload, this catches edge cases (new workers, reloader, etc).
    """
    try:
        import frappe
        is_wl = api.send_dispatch_whatsapp in frappe.whitelisted if hasattr(frappe, 'whitelisted') else False

        if not is_wl:
            # Force re-import if somehow missing (shouldn't happen with module-level import)
            from . import api  # noqa: F401
            frappe.logger("kreativ_notification").warning(
                f"API was missing from whitelist, re-imported. Worker {os.getpid()}"
            )
    except Exception as e:
        import frappe
        frappe.logger("kreativ_notification").error(f"ensure_api_loaded failed: {e}", exc_info=True)