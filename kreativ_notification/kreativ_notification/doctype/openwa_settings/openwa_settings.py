# Copyright (c) 2024, Kreativ Gravures
# License: MIT

import frappe
from frappe.model.document import Document

from kreativ_notification.notification.security import verify_webhook_signature


class OpenWASettings(Document):
	pass


@frappe.whitelist()
def get_session_status():
	"""Fetch current session status from OpenWA gateway."""
	frappe.only_for(("System Manager", "HR Manager"))
	from kreativ_notification.notification.openwa_client import get_openwa_config
	base_url, api_key, session_id = get_openwa_config()

	if not base_url or not api_key:
		return {"status": "error", "message": "Base URL and/or API Key not configured"}

	import requests
	try:
		r = requests.get(f"{base_url}/api/sessions/{session_id}",
						 headers={"X-API-Key": api_key}, timeout=10)
		if r.status_code == 404:
			return {"status": "not_found", "message": "Session not found on OpenWA (may have been deleted)."}
		if r.status_code != 200:
			return {"status": "error", "message": f"Session API returned {r.status_code}: {r.text[:200]}"}

		data = r.json()
		return {
			"status": data.get("status", "unknown"),
			"phone": data.get("phone"),
			"pushname": data.get("pushname"),
			"last_active": data.get("lastActive"),
			"session_id": data.get("id"),
			"session_name": data.get("name"),
		}
	except Exception as e:
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def get_session_qr():
	"""Get QR code image for the session (base64 data URL)."""
	frappe.only_for(("System Manager", "HR Manager"))
	from kreativ_notification.notification.openwa_client import get_openwa_config
	base_url, api_key, session_id = get_openwa_config()

	if not base_url or not api_key:
		return {"status": "error", "message": "Base URL and/or API Key not configured"}

	import requests
	try:
		r = requests.get(f"{base_url}/api/sessions/{session_id}/qr",
						 headers={"X-API-Key": api_key}, timeout=10)
		if r.status_code == 404:
			return {"status": "error", "message": "Session not found on OpenWA"}
		if r.status_code != 200:
			return {"status": "error", "message": f"QR API returned {r.status_code}: {r.text[:200]}"}

		data = r.json()
		qr_code = data.get("qrCode", "")
		return {
			"status": "ok",
			"qr": qr_code,
			"session_status": data.get("status", "qr_ready"),
		}
	except Exception as e:
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def start_session():
	"""Start/Restart the WhatsApp session."""
	frappe.only_for(("System Manager", "HR Manager"))
	from kreativ_notification.notification.openwa_client import get_openwa_config
	base_url, api_key, session_id = get_openwa_config()

	if not base_url or not api_key:
		return {"status": "error", "message": "Base URL and/or API Key not configured"}

	import requests
	try:
		r = requests.post(f"{base_url}/api/sessions/{session_id}/start",
						  headers={"X-API-Key": api_key}, timeout=15)
		if r.status_code in (200, 201):
			data = r.json()
			return {"status": "ok", "message": f"Session start requested. New status: {data.get('status', 'unknown')}"}
		return {"status": "error", "message": f"Start returned {r.status_code}: {r.text[:200]}"}
	except Exception as e:
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def stop_session():
	"""Stop the WhatsApp session."""
	frappe.only_for(("System Manager", "HR Manager"))
	from kreativ_notification.notification.openwa_client import get_openwa_config
	base_url, api_key, session_id = get_openwa_config()

	if not base_url or not api_key:
		return {"status": "error", "message": "Base URL and/or API Key not configured"}

	import requests
	try:
		r = requests.post(f"{base_url}/api/sessions/{session_id}/stop",
						  headers={"X-API-Key": api_key}, timeout=10)
		if r.status_code in (200, 204):
			return {"status": "ok", "message": "Session stopped successfully"}
		return {"status": "error", "message": f"Stop returned {r.status_code}: {r.text[:200]}"}
	except Exception as e:
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def test_webhook_config():
	"""Test if webhook settings are properly configured."""
	frappe.only_for(("System Manager", "HR Manager"))
	settings = frappe.get_single("OpenWA Settings")

	if not settings.webhook_enabled:
		return {"status": "warning", "message": "Webhook is not enabled in settings"}

	if not settings.webhook_secret:
		return {"status": "error", "message": "Webhook secret is not configured"}

	if not settings.auto_reply_enabled:
		return {"status": "warning", "message": "Auto-reply is not enabled"}

	# Check if circuit breaker is tripped
	from kreativ_notification.notification.openwa_client import check_circuit_breaker
	if check_circuit_breaker():
		return {"status": "error", "message": "Circuit breaker is tripped - outbound messages blocked"}

	return {"status": "ok", "message": "Webhook configuration looks good"}


def auto_refresh_session_status():
	"""Scheduled job: auto-refresh session status in OpenWA Settings (runs every 10 min)."""
	try:
		from kreativ_notification.notification.openwa_client import get_openwa_config
		import requests

		base_url, api_key, session_id = get_openwa_config()
		if not base_url or not api_key:
			return

		r = requests.get(f"{base_url}/api/sessions/{session_id}",
						 headers={"X-API-Key": api_key}, timeout=10)
		if r.status_code != 200:
			return

		data = r.json()
		status = data.get("status", "unknown")
		phone = data.get("phone")
		pushname = data.get("pushname")

		# Update the Single DocType directly
		frappe.db.set_value("OpenWA Settings", "OpenWA Settings", {
			"session_status": status,
			"session_phone": phone or "",
			"session_pushname": pushname or "",
		}, update_modified=False)
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="Auto-refresh session status failed", message=frappe.get_traceback())