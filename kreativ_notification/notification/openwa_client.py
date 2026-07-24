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

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 session_id: str | None = None):
        """Explicit args win; anything blank falls back to OpenWA Settings.

        Supports every historical call style:
            OpenWAClient()                          # settings-driven (legacy)
            OpenWAClient(base_url, api_key)         # api.py style
            OpenWAClient(base_url, api_key, sid)    # per-channel (driver)
        """
        cfg_url, cfg_key, cfg_session = get_openwa_config()
        url = base_url or cfg_url
        self.base_url = url.rstrip("/") if url else ""
        self.api_key = api_key or cfg_key
        self.session_id = session_id or cfg_session or "default"

    def _ensure_configured(self) -> None:
        if not self.base_url:
            frappe.throw(_("OpenWA Base URL is not configured."))
        if not self.api_key:
            frappe.throw(_("OpenWA API Key is not configured."))

    @staticmethod
    def _classify_error(error: str) -> dict:
        """Classify an error message to detect permanent vs transient failures.

        Returns dict with keys: success (bool), error (str), permanent (bool).
        """
        error_lower = error.lower()
        # Permanent: contact not on WhatsApp (OpenWA "No LID for user" -> HTTP 500)
        if "no lid for user" in error_lower:
            return {"success": False, "error": error, "permanent": True}

        # Transient: gateway down, timeout, 5xx server errors
        if any(keyword in error_lower for keyword in [
            "cannot connect", "connection", "timeout", "timed out",
            "http 500", "http 502", "http 503", "http 504",
        ]):
            return {"success": False, "error": error, "permanent": False}

        # Permanent: invalid number, not registered, auth failure, not found
        if any(keyword in error_lower for keyword in [
            "invalid number", "not registered", "not on whatsapp",
            "http 400", "http 401", "http 404",
            "unauthorized", "auth", "permission",
        ]):
            return {"success": False, "error": error, "permanent": True}

        # Unknown: treat as transient (safer to retry)
        return {"success": False, "error": error, "permanent": False}

    def _post(self, endpoint: str, payload: dict, timeout: int = 30) -> dict[str, Any]:
        self._ensure_configured()
        url = "{0}/api/sessions/{1}/messages/{2}".format(
            self.base_url, self.session_id, endpoint,
        )
        headers = {"X-API-Key": self.api_key}
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if r.ok:
                return {"success": True, "data": r.json() if r.content else {}, "permanent": False}
            _log_error("OpenWA HTTP {0}".format(r.status_code),
                       "URL: {0}\nStatus: {1}\nResponse: {2}".format(url, r.status_code, r.text[:500]))
            return self._classify_error("HTTP {0}: {1}".format(r.status_code, r.text[:200]))
        except requests.exceptions.ConnectionError:
            return self._classify_error("Cannot connect to OpenWA at {0}. Is it running?".format(self.base_url))
        except requests.exceptions.Timeout:
            return self._classify_error("OpenWA timed out after {0}s".format(timeout))
        except Exception as e:
            _log_error("OpenWA exception", frappe.get_traceback())
            return self._classify_error("Unexpected error — check Error Log: {0}".format(e))

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

    def get_session_state(self) -> dict:
        """GET /api/sessions/{id}/status -> {"status": ..., ...} (never throws)."""
        self._ensure_configured()
        url = "{0}/api/sessions/{1}/status".format(self.base_url, self.session_id)
        try:
            r = requests.get(url, headers={"X-API-Key": self.api_key}, timeout=10)
            if r.status_code == 404:
                return {"status": "not_found"}
            if r.ok:
                data = r.json() if r.content else {}
                return data if isinstance(data, dict) else {"status": "unknown"}
            return {"status": "error", "detail": "HTTP {0}".format(r.status_code)}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

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

    def check_contact(self, chat_id: str) -> dict:
        """Check if a contact exists on WhatsApp via OpenWA.

        Returns: {"success": bool, "exists": bool, "error": str|None}
        """
        self._ensure_configured()
        # OpenWA contacts endpoint returns all contacts - we can filter
        # For a more direct check, use the /contacts/{id}/profile-picture endpoint
        # which returns 404 if contact doesn't exist on WhatsApp
        url = "{0}/api/sessions/{1}/contacts/{2}/profile-picture".format(
            self.base_url, self.session_id, chat_id)
        try:
            r = requests.get(url, headers={"X-API-Key": self.api_key}, timeout=10)
            if r.status_code == 404:
                return {"success": True, "exists": False, "error": None}
            if r.ok:
                return {"success": True, "exists": True, "error": None}
            return self._classify_error("HTTP {0}: {1}".format(r.status_code, r.text[:200]))
        except requests.exceptions.ConnectionError:
            return self._classify_error("Cannot connect to OpenWA at {0}. Is it running?".format(self.base_url))
        except requests.exceptions.Timeout:
            return self._classify_error("OpenWA timed out after 10s")
        except Exception as e:
            _log_error("OpenWA exception", frappe.get_traceback())
            return self._classify_error("Unexpected error — check Error Log: {0}".format(e))

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


def _log_error(title: str, message: str) -> None:
    try:
        frappe.log_error(title=title, message=message)
    except Exception:
        pass