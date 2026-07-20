# -*- coding: utf-8 -*-
# Copyright (c) 2026, Kreativ Gravures and contributors
# For license information, please see license.txt

"""Canonical OpenWA Client with Circuit Breaker."""

import frappe
from frappe import _
from frappe.utils import now_datetime
import requests
import base64
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Circuit Breaker — single source of truth
# ---------------------------------------------------------------------------

CIRCUIT_BREAKER_THRESHOLD = 3


def _breaker_key(suffix: str) -> str:
    """Per-site cache key so sites don't share breaker state."""
    site = frappe.local.site or "default"
    return f"openwa:{suffix}:{site}"


def check_circuit_breaker() -> None:
    """Fail-fast if OpenWA has been failing consecutively >= threshold."""
    streak = int(frappe.cache().get_value(_breaker_key("streak")) or 0)
    if streak >= CIRCUIT_BREAKER_THRESHOLD:
        frappe.throw(
            _("WhatsApp service is temporarily unavailable (circuit breaker open). "
              "Please try again later. If the issue persists, check OpenWA health.")
        )


def increment_circuit_breaker() -> int:
    streak = _get_failure_streak() + 1
    frappe.cache().set_value(_breaker_key("streak"), streak, expires_in_sec=86400)
    if streak == CIRCUIT_BREAKER_THRESHOLD:
        frappe.cache().set_value(
            _breaker_key("tripped"), str(now_datetime()), expires_in_sec=86400)
    return streak


def reset_circuit_breaker() -> None:
    frappe.cache().delete_value(_breaker_key("streak"))
    frappe.cache().delete_value(_breaker_key("tripped"))
    frappe.cache().delete_value(_breaker_key("probe"))


def _get_failure_streak() -> int:
    return int(frappe.cache().get_value(_breaker_key("streak")) or 0)


# ---------------------------------------------------------------------------
# OpenWA Config — shared helper
# ---------------------------------------------------------------------------

def get_openwa_config() -> tuple[str, str, str]:
    """Return (base_url, api_key, session_id) — any may be blank."""
    try:
        settings = frappe.get_cached_doc("OpenWA Settings")
        base_url = (settings.get("base_url") or "").strip().rstrip("/")
        api_key = settings.get_password("api_key", raise_exception=False) or ""
        session_id = (settings.get("session_id") or "default").strip()
        return base_url, api_key, session_id
    except frappe.DoesNotExistError:
        return "", "", ""


# ---------------------------------------------------------------------------
# OpenWAClient — single code path for all OpenWA HTTP calls
# ---------------------------------------------------------------------------

class OpenWAClient:
    """Consolidated OpenWA gateway client.

    Usage::

        client = OpenWAClient()
        result = client.send_text(chat_id, text="Hello")
        result = client.send_document(chat_id, pdf_b64, "invoice.pdf")
    """

    def __init__(self):
        self.base_url, self.api_key, self.session_id = get_openwa_config()

    def _ensure_configured(self) -> None:
        if not self.base_url:
            frappe.throw(_("OpenWA Base URL is not configured."))
        if not self.api_key:
            frappe.throw(_("OpenWA API Key is not configured."))

    def _post(self, endpoint: str, payload: dict, timeout: int = 30) -> dict[str, Any]:
        self._ensure_configured()
        url = "{0}/api/sessions/{1}/messages/{2}".format(
            self.base_url, self.session_id, endpoint,
        )
        headers = {"X-API-Key": self.api_key}
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if r.ok:
                return {"success": True, "data": r.json() if r.content else {}}
            _log_error("OpenWA HTTP {0}".format(r.status_code),
                       "URL: {0}\nStatus: {1}\nResponse: {2}".format(url, r.status_code, r.text[:500]))
            return {"success": False, "error": "HTTP {0}: {1}".format(r.status_code, r.text[:200])}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Cannot connect to OpenWA at {0}. Is it running?".format(self.base_url)}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "OpenWA timed out after {0}s".format(timeout)}
        except Exception:
            _log_error("OpenWA exception", frappe.get_traceback())
            return {"success": False, "error": "Unexpected error — check Error Log."}

    def send_text(self, chat_id: str, text: str) -> dict:
        return self._post("send-text", {"chatId": chat_id, "text": text})

    def send_document(self, chat_id: str, base64_data: str, filename: str,
                      mimetype: str = "application/pdf", caption: str = "") -> dict:
        return self._post("send-document", {
            "chatId": chat_id,
            "base64": base64_data,
            "mimetype": mimetype,
            "filename": filename,
            "caption": caption,
        })

    def send_image(self, chat_id: str, base64_data: str, filename: str, caption: str = "") -> dict:
        return self.send_document(chat_id, base64_data, filename,
                                  mimetype="image/png", caption=caption)

    def get_contacts(self, limit: int = 200) -> list:
        self._ensure_configured()
        url = "{0}/api/sessions/{1}/contacts?limit={2}".format(self.base_url, self.session_id, limit)
        try:
            r = requests.get(url, headers={"X-API-Key": self.api_key}, timeout=15)
            if r.ok:
                return r.json() if isinstance(r.json(), list) else []
        except Exception:
            pass
        return []

    def get_chats(self, search: str | None = None) -> dict:
        """Fetch recent chats from OpenWA /chats endpoint for contact picker.
        
        Returns:
            {"chats": [...], "groups": [...]} with last message, timestamp, unread count
        """
        self._ensure_configured()
        cache_key = "openwa_chats_list"
        if search:
            cache_key = "openwa_chats_search_{}".format(frappe.safe_encode(search)[:50])
        cached = frappe.cache().get_value(cache_key)
        if cached:
            return cached

        url = "{0}/api/sessions/{1}/chats".format(self.base_url.rstrip("/"), self.session_id)
        try:
            r = requests.get(url, headers={"X-API-Key": self.api_key}, timeout=15)
            if r.ok:
                chats = r.json() if isinstance(r.json(), list) else []
                seen = set()
                search_lower = (search or "").lower()
                result = {"chats": [], "groups": []}

                for c in chats:
                    cid = c.get("id", "")
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)

                    name = c.get("name", "") or c.get("pushname", "") or cid
                    is_group = c.get("isGroup", False) or "@g.us" in cid
                    last_msg = c.get("lastMessage", {}).get("body", "") if isinstance(c.get("lastMessage"), dict) else ""
                    timestamp = c.get("timestamp", 0)
                    unread = c.get("unreadCount", 0)

                    if search and search_lower not in name.lower() and search_lower not in cid and search_lower not in (last_msg or "").lower():
                        continue

                    item = {
                        "id": cid,
                        "name": name,
                        "isGroup": is_group,
                        "lastMessage": last_msg,
                        "timestamp": timestamp,
                        "unreadCount": unread,
                    }
                    if is_group:
                        result["groups"].append(item)
                    else:
                        result["chats"].append(item)

                # Sort by timestamp descending
                result["chats"].sort(key=lambda c: c.get("timestamp") or 0, reverse=True)
                result["groups"].sort(key=lambda g: g.get("timestamp") or 0, reverse=True)

                frappe.cache().set_value(cache_key, result, expires_in_sec=300)
                return result
        except Exception:
            pass

        return {"chats": [], "groups": []}

    def search_contacts(self, query: str) -> dict:
        """Search contacts via OpenWA /chats endpoint with query filter."""
        return self.get_chats(search=query)

    def get_session_status(self) -> dict:
        self._ensure_configured()
        url = "{0}/api/sessions/{1}".format(self.base_url, self.session_id)
        try:
            r = requests.get(url, headers={"X-API-Key": self.api_key}, timeout=10)
            if r.status_code == 404:
                return {"status": "not_found", "message": "Session not found on OpenWA."}
            if r.ok:
                data = r.json()
                return {
                    "status": data.get("status", "unknown"),
                    "phone": data.get("phone"),
                    "pushname": data.get("pushname"),
                    "last_active": data.get("lastActive"),
                    "session_id": self.session_id,
                }
            return {"status": "error", "message": "HTTP {0}".format(r.status_code)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_session_qr(self) -> dict:
        self._ensure_configured()
        url = "{0}/api/sessions/{1}/qr".format(self.base_url, self.session_id)
        try:
            r = requests.get(url, headers={"X-API-Key": self.api_key}, timeout=10)
            if r.ok:
                data = r.json()
                return {"status": "ok", "qr": data.get("qrCode", ""),
                        "session_status": data.get("status", "qr_ready")}
            return {"status": "error", "message": "HTTP {0}: {1}".format(r.status_code, r.text[:200])}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def start_session(self) -> dict:
        self._ensure_configured()
        url = "{0}/api/sessions/{1}/start".format(self.base_url, self.session_id)
        try:
            r = requests.post(url, headers={"X-API-Key": self.api_key}, timeout=15)
            if r.ok:
                return {"status": "ok", "message": "Session start requested."}
            return {"status": "error", "message": "HTTP {0}".format(r.status_code)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def stop_session(self) -> dict:
        self._ensure_configured()
        url = "{0}/api/sessions/{1}/stop".format(self.base_url, self.session_id)
        try:
            r = requests.post(url, headers={"X-API-Key": self.api_key}, timeout=10)
            if r.ok:
                return {"status": "ok", "message": "Session stopped."}
            return {"status": "error", "message": "HTTP {0}".format(r.status_code)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def create_session(self, name: str = "") -> dict:
        self._ensure_configured()
        name = name or frappe.local.site or "default"
        url = "{0}/api/sessions".format(self.base_url)
        try:
            r = requests.post(url,
                              headers={"Content-Type": "application/json", "X-API-Key": self.api_key},
                              json={"name": name}, timeout=15)
            if r.status_code != 201:
                return {"status": "error", "message": "Create returned {0}: {1}".format(r.status_code, r.text[:300])}
            data = r.json()
            new_id = data.get("id")
            if not new_id:
                return {"status": "error", "message": "No session ID returned."}
            settings = frappe.get_single("OpenWA Settings")
            settings.db_set("session_id", new_id, commit=True)
            self.session_id = new_id
            self.start_session()
            return {"status": "ok", "new_session_id": new_id, "session_name": data.get("name")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def fetch_profile_picture(self, contact_id: str) -> str | None:
        if "@g.us" in contact_id:
            return None
        url = "{0}/api/sessions/{1}/contacts/{2}/profile-picture".format(
            self.base_url, self.session_id, contact_id)
        try:
            r = requests.get(url, headers={"X-API-Key": self.api_key}, timeout=10)
            if r.ok:
                return r.json().get("url")
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Background queue worker
# ---------------------------------------------------------------------------

WHATSAPP_QUEUE = "long"
WHATSAPP_TIMEOUT = 1500


def enqueue_whatsapp_send(action_type: str, log_name: str | None = None, **kwargs) -> dict:
    """Enqueue a WhatsApp send as a background job."""
    check_circuit_breaker()

    from kreativ_notification.notification.send_log import create_log as _log_whatsapp_send

    if not log_name:
        log_name = _log_whatsapp_send(
            source_doctype=kwargs.pop("source_doctype", "System"),
            source_docname=kwargs.pop("source_docname", ""),
            recipient=kwargs.pop("recipient", ""),
            message_type=kwargs.pop("message_type", "Custom"),
            meta=kwargs.pop("meta", {}),
        )
        frappe.db.commit()

    job = frappe.enqueue(
        "kreativ_notification.notification.openwa_client._execute_whatsapp_send",
        queue=WHATSAPP_QUEUE,
        timeout=WHATSAPP_TIMEOUT,
        job_id=log_name or None,
        job_args={"action_type": action_type, "log_name": log_name, **kwargs},
    )
    return {
        "status": "queued",
        "job_id": job.id,
        "log_name": log_name,
        "message": _("WhatsApp send queued. Check WhatsApp Send Log for status."),
    }


def _execute_whatsapp_send(job_args: dict) -> dict:
    """Background worker — runs via frappe.enqueue."""
    action = job_args.pop("action_type", None)
    log_name = job_args.pop("log_name", None)

    _update_log(log_name, "Processing")

    try:
        if action == "send_pdf":
            result = _bg_send_pdf(job_args)
        elif action == "send_screenshot":
            result = _bg_send_screenshot(job_args)
        elif action == "send_test":
            result = _bg_send_test()
        elif action == "send_manual":
            result = _bg_send_manual(job_args)
        else:
            result = {"success": False, "error": "Unknown action: {0}".format(action)}

        if result.get("success"):
            reset_circuit_breaker()
            _update_log(log_name, "Sent")
        else:
            increment_circuit_breaker()
            _update_log(log_name, "Failed", result.get("error"))
        return result

    except Exception as e:
        increment_circuit_breaker()
        _update_log(log_name, "Failed", str(e))
        frappe.log_error(title="WhatsApp bg worker error", message=frappe.get_traceback())
        return {"success": False, "error": str(e)}


def _bg_send_pdf(args: dict) -> dict:
    from kreativ_notification.notification.pdf_utils import generate_pdf_bytes
    pdf_bytes = generate_pdf_bytes(args["doctype"], args["name"], args.get("print_format"))
    if not pdf_bytes or len(pdf_bytes) < 1024:
        return {"success": False, "error": "Generated PDF is empty."}
    b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    filename = "{0}_{1}.pdf".format(args["doctype"].replace(" ", "_"), args["name"])
    chat_id = args.get("chat_id")
    if not chat_id:
        chat_id = frappe.get_cached_doc("OpenWA Settings").chat_id
    client = OpenWAClient()
    return client.send_document(chat_id, b64, filename, caption=filename)


def _bg_send_screenshot(args: dict) -> dict:
    from kreativ_notification.notification.screenshot_utils import screenshot_html_playwright
    png = screenshot_html_playwright(args["html"], width=args.get("width", 1000))
    b64 = base64.b64encode(png).decode("utf-8")
    if len(b64) < 1024:
        return {"success": False, "error": "Generated screenshot is empty."}
    chat_id = args.get("chat_id") or frappe.get_cached_doc("OpenWA Settings").chat_id
    client = OpenWAClient()
    return client.send_image(chat_id, b64, args["filename"], args.get("caption", ""))


def _bg_send_test() -> dict:
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not settings.chat_id:
        return {"success": False, "error": "No Recipient Chat ID in OpenWA Settings."}
    client = OpenWAClient()
    return client.send_text(settings.chat_id, "Test message from Kreativ Notification")


def _bg_send_manual(args: dict) -> dict:
    client = OpenWAClient()
    chat_id = args["chat_id_override"]
    file_b64 = args.get("file_b64", "")
    filename = args.get("filename", "document")
    message_type = args.get("message_type", "Custom")

    if message_type in ("Print PDF", "Dispatch PDF"):
        return client.send_document(chat_id, file_b64, filename, caption=filename)
    elif message_type == "Screenshot":
        return client.send_image(chat_id, file_b64, filename, args.get("caption", ""))
    else:
        if file_b64:
            return client.send_document(chat_id, file_b64, filename)
        return client.send_text(chat_id, args.get("text", ""))


def _update_log(log_name: str | None, status: str, error: str | None = None) -> None:
    if not log_name:
        return
    try:
        frappe.db.set_value("WhatsApp Send Log", log_name, {
            "status": status,
            "error_message": error or "",
        }, update_modified=False)
        frappe.db.commit()
    except Exception:
        pass


def _log_error(title: str, message: str) -> None:
    try:
        frappe.log_error(title=title, message=message)
    except Exception:
        pass