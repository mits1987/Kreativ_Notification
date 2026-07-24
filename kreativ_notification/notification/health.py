"""OpenWA Health Check - runs every 5 minutes via scheduler.

Circuit Breaker (per-site):
    Tracks consecutive health check failures PER SITE using scoped cache keys
    (openwa:streak:{site}). After 3 consecutive failures, trips the breaker
    and enters exponential backoff (5min, 10min, 20min...) to avoid hammering
    a down OpenWA instance. Reset on first success.

Auto-Recovery:
    1. Session 404 (lost/deleted) -> After 3 consecutive 404s (~15 min),
       auto-creates a new session on OpenWA, updates Frappe settings,
       and logs a prominent error: "QR SCAN NEEDED" for admin to re-link WhatsApp.
    2. Session "created"/"disconnected" -> Auto-starts via POST /start
       (works when multi-device credentials still exist on disk).
    3. Session stale (lastActive > 240 min / 4 hours) -> Stop/start cycle via API.
       (Idle WhatsApp accounts are normal overnight/weekends; 4h threshold
       avoids unnecessary reconnect churn that drops pairings.)
    4. Session "ready"/"connected" -> Healthy, reset breaker.

Per-Site Isolation:
    Each site (kreativ216, kreativ316) has its own OpenWA session
    and circuit breaker state. One site's session going down no longer
    affects the other.

Multi-Site Guard:
    If both sites share same base_url AND session_id == "default",
    health check refuses to act (logs ERROR, returns guarded state).
    This prevents one site's health check from stopping/starting/recreating
    the other site's session. Admin MUST configure unique session_id per site.

Gateway Safety:
    Auto-restart of the gateway process is DISABLED in health checks to
    prevent one site from killing the other's connection. Gateway
    restarts must be done manually via 'sudo supervisorctl restart openwa'.
    The gateway is now managed by supervisor for automatic crash recovery.
"""
import frappe
import random
import requests
from frappe.utils import get_datetime, now
from datetime import datetime, timezone
import time

from kreativ_notification.notification.openwa_client import (
    CIRCUIT_BREAKER_THRESHOLD,
    _breaker_key,
    _get_failure_streak,
    increment_circuit_breaker as _increment_failure_streak,
    reset_circuit_breaker as _reset_failure_streak,
)

MAX_BACKOFF_MINUTES = 60
MAX_BREAKER_DURATION_MINUTES = 60
# Number of consecutive 404s before auto-creating new session
CONSECUTIVE_404_THRESHOLD = 3
# Stale threshold: 4 hours (was 60 min - too aggressive for idle accounts)
STALE_THRESHOLD_MINUTES = 240


def _is_breaker_tripped() -> bool:
    return _get_failure_streak() >= CIRCUIT_BREAKER_THRESHOLD


def _calculate_backoff_minutes() -> int:
    streak = _get_failure_streak()
    if streak < CIRCUIT_BREAKER_THRESHOLD:
        return 5
    backoff_minutes = 5 * (2 ** (streak - CIRCUIT_BREAKER_THRESHOLD))
    return min(backoff_minutes, MAX_BACKOFF_MINUTES)


def _can_attempt_probe() -> bool:
    if not _is_breaker_tripped():
        return True

    breaker_tripped_at = frappe.cache().get_value(_breaker_key("tripped"))
    if breaker_tripped_at:
        try:
            elapsed_minutes = (get_datetime() - get_datetime(breaker_tripped_at)).total_seconds() / 60
            if elapsed_minutes > MAX_BREAKER_DURATION_MINUTES:
                _reset_failure_streak()
                frappe.log_error(
                    title="OpenWA Circuit Breaker Auto-Reset",
                    message=(
                        f"Breaker was tripped for {elapsed_minutes:.0f} minutes "
                        f"(>{MAX_BREAKER_DURATION_MINUTES} min ceiling). "
                        f"Force-resetting to allow recovery probe."
                    ),
                )
                frappe.cache().set_value(_breaker_key("probe"), str(get_datetime()))
                return True
        except Exception:
            pass

    last_probe = frappe.cache().get_value(_breaker_key("probe"))
    if not last_probe:
        frappe.cache().set_value(_breaker_key("probe"), str(get_datetime()))
        return True

    backoff = _calculate_backoff_minutes()
    jittered_backoff = backoff * random.uniform(0.6, 1.4)
    try:
        elapsed = (get_datetime() - get_datetime(last_probe)).total_seconds() / 60
    except Exception:
        elapsed = jittered_backoff + 1

    if elapsed >= jittered_backoff:
        frappe.cache().set_value(_breaker_key("probe"), str(get_datetime()))
        return True

    return False


def _get_consecutive_404s() -> int:
    """Track consecutive 404s for this site's session."""
    return int(frappe.cache().get_value(_breaker_key("404_streak")) or 0)


def _increment_404_streak() -> int:
    streak = _get_consecutive_404s() + 1
    frappe.cache().set_value(_breaker_key("404_streak"), streak, expires_in_sec=86400)
    return streak


def _reset_404_streak() -> None:
    frappe.cache().delete_value(_breaker_key("404_streak"))


def _check_multi_site_collision(settings) -> bool:
    """Guard: if session_id is 'default' and base_url same as other sites, refuse action.

    Returns True if collision detected (should skip auto-recovery).
    """
    session_id = settings.session_id or "default"
    if session_id != "default":
        return False

    # Check if other sites exist with same base_url
    other_sites = frappe.get_all("OpenWA Settings",
        filters={"base_url": settings.base_url, "enabled": 1},
        pluck="name"
    )
    if len(other_sites) > 1:
        frappe.log_error(
            title="OpenWA Multi-Site Collision Detected",
            message=(
                f"Site {frappe.local.site}: session_id is 'default' but other sites "
                f"share base_url ({settings.base_url}). "
                f"Sites: {', '.join(other_sites)}. "
                f"Auto-recovery SKIPPED to prevent cross-site session stomping. "
                f"Configure unique session_id per site in OpenWA Settings."
            ),
        )
        return True
    return False


def _session_is_stale(settings, data: dict) -> bool:
    last_active = data.get("lastActive")
    if not last_active:
        return False

    last_dt = get_datetime(last_active)
    age_minutes = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60

    if age_minutes > STALE_THRESHOLD_MINUTES:
        frappe.cache().set_value(_breaker_key("stale"), True, expires_in_sec=7200)
        return True

    frappe.cache().delete_value(_breaker_key("stale"))
    return False


def _restart_session(settings) -> dict:
    base_url = settings.base_url.rstrip("/")
    api_key = settings.get_password("api_key", raise_exception=False) or ""
    session_id = settings.session_id or "default"
    headers = {"X-API-Key": api_key}

    try:
        r = requests.post(f"{base_url}/api/sessions/{session_id}/stop", headers=headers, timeout=10)
        if r.status_code not in (200, 204):
            return {"status": "stale", "reason": f"Stop returned {r.status_code}"}
    except Exception as e:
        return {"status": "stale", "reason": f"Stop error: {e}"}

    time.sleep(2)

    try:
        r = requests.post(f"{base_url}/api/sessions/{session_id}/start", headers=headers, timeout=15)
    except Exception as e:
        return {"status": "stale", "reason": f"Start error: {e}"}

    time.sleep(2)

    try:
        r = requests.get(f"{base_url}/api/sessions/{session_id}", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            new_status = data.get("status", "")
            last_active = data.get("lastActive", "")

            if new_status in ("ready", "connected") and last_active:
                last_dt = get_datetime(last_active)
                age_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if age_seconds < 120:
                    frappe.log_error(
                        title="OpenWA Session Recovered",
                        message=f"Auto-recovered via stop/start. lastActive={last_active}",
                    )
                    frappe.cache().delete_value(_breaker_key("stale"))
                    return {"status": "recovered", "lastActive": last_active}
    except Exception:
        pass

    return {"status": "stale", "reason": "Session not active after restart"}


def _retry_unsent():
    try:
        from kreativ_notification.notification.employee_notifications import retry_missed_notifications
        retry_missed_notifications()
    except Exception:
        pass


def check_openwa_session():
    """
    Scheduled job: runs every 5 minutes to verify OpenWA session is healthy.

    Circuit Breaker: Tracks consecutive failures. After 3 failures, trips
    and enters exponential backoff (5min, 10min, 20min...) to avoid
    hammering a down OpenWA instance. Reset on first success.
    """
    if _is_breaker_tripped() and not _can_attempt_probe():
        backoff = _calculate_backoff_minutes()
        frappe.logger().info(
            f"OpenWA Circuit Breaker Open — Health check skipped. "
            f"{_get_failure_streak()} consecutive failures, "
            f"backing off for {backoff} minutes."
        )
        return {
            "status": "circuit_open",
            "failure_streak": _get_failure_streak(),
            "backoff_minutes": backoff,
            "checked": now(),
        }

    try:
        settings = frappe.get_cached_doc("OpenWA Settings")
        if not settings.enabled:
            _reset_failure_streak()
            _reset_404_streak()
            return {"status": "skipped", "reason": "OpenWA not enabled"}

        base_url = settings.base_url.rstrip("/") if settings.base_url else ""
        api_key = settings.get_password("api_key", raise_exception=False) or ""
        session_id = settings.session_id or "default"

        if not base_url or not api_key:
            _increment_failure_streak()
            _reset_404_streak()
            return {"status": "error", "reason": "Missing base_url or api_key in settings"}

        # 1. Check HTTP endpoint
        try:
            r = requests.get(f"{base_url}/", timeout=10)
            if r.status_code != 200:
                _increment_failure_streak()
                _reset_404_streak()
                frappe.log_error(
                    title="OpenWA Health Check Failed",
                    message=f"HTTP {r.status_code} from {base_url}. Gateway may be down — restart manually if persistent.",
                )
                return {"status": "error", "reason": f"HTTP {r.status_code}"}
        except Exception as e:
            _increment_failure_streak()
            _reset_404_streak()
            frappe.log_error(
                title="OpenWA Health Check Failed",
                message=f"HTTP check failed: {e}. Gateway may be down — restart manually if persistent.",
            )
            return {"status": "error", "reason": f"HTTP check failed: {e}"}

        # 2. Check session status
        try:
            r = requests.get(
                f"{base_url}/api/sessions/{session_id}",
                headers={"X-API-Key": api_key},
                timeout=10
            )

            # --- Session not found on server (404) ---
            if r.status_code == 404:
                # Increment consecutive 404 counter
                streak = _increment_404_streak()

                if streak < CONSECUTIVE_404_THRESHOLD:
                    frappe.logger().info(
                        f"OpenWA session {session_id} 404 (streak: {streak}/{CONSECUTIVE_404_THRESHOLD}). "
                        f"Waiting for {CONSECUTIVE_404_THRESHOLD - streak} more consecutive 404s before auto-create."
                    )
                    return {"status": "404_counting", "streak": streak, "threshold": CONSECUTIVE_404_THRESHOLD, "checked": now()}

                # Threshold reached - attempt auto-create with multi-site guard
                site_name = frappe.local.site or "default"
                frappe.log_error(
                    title="OpenWA Session Lost — Auto-Creating After Threshold",
                    message=(
                        f"Session {session_id} not found on OpenWA server after "
                        f"{CONSECUTIVE_404_THRESHOLD} consecutive 404s (~15 min). "
                        f"Attempting to create a new session for site {site_name}. "
                        f"QR scan will be needed to re-link WhatsApp."
                    ),
                )

                # Multi-site collision guard
                if _check_multi_site_collision(settings):
                    _reset_404_streak()
                    return {"status": "guarded", "reason": "Multi-site collision - manual intervention required", "checked": now()}

                try:
                    create_r = requests.post(
                        f"{base_url}/api/sessions",
                        headers={"Content-Type": "application/json", "X-API-Key": api_key},
                        json={"name": site_name},
                        timeout=10
                    )
                    if create_r.status_code == 201:
                        new_session = create_r.json()
                        new_id = new_session.get("id", "")
                        if new_id:
                            settings.db_set("session_id", new_id, commit=True)
                            requests.post(
                                f"{base_url}/api/sessions/{new_id}/start",
                                headers={"X-API-Key": api_key},
                                timeout=10
                            )
                            frappe.log_error(
                                title="OpenWA New Session Created — QR SCAN NEEDED",
                                message=(
                                    f"New session created: {new_session.get('name', '')} (id: {new_id}). "
                                    f"Status: {new_session.get('status', '')}. "
                                    f"Updated OpenWA Settings with new session_id. "
                                    f"YOU MUST SCAN THE QR CODE at {base_url}/ to link WhatsApp. "
                                    f"Until scanned, the session will stay in disconnected state."
                                ),
                            )
                            # Reset 404 streak on successful creation
                            _reset_404_streak()
                            # Reset failure streak - this is a successful recovery action
                            _reset_failure_streak()
                            return {"status": "qr_needed", "new_session_id": new_id, "checked": now()}
                    else:
                        frappe.log_error(
                            title="OpenWA Session Creation Failed",
                            message=f"POST /api/sessions returned {create_r.status_code}: {create_r.text[:300]}",
                        )
                except Exception as create_e:
                    frappe.log_error(
                        title="OpenWA Session Creation Error",
                        message=f"Exception creating session: {create_e}",
                    )
                return {"status": "error", "reason": "Session lost and re-creation failed"}

            # Success - reset 404 streak
            _reset_404_streak()

            if r.status_code != 200:
                _increment_failure_streak()
                frappe.log_error(
                    title="OpenWA Health Check Failed",
                    message=f"Session API returned {r.status_code}. Restart manually if persistent.",
                )
                return {"status": "error", "reason": f"Session API {r.status_code}"}

            data = r.json()
            status = data.get("status", "")

            # --- Auto-start if session is created/disconnected (credentials may still exist) ---
            if status in ["created", "disconnected"]:
                try:
                    start_r = requests.post(
                        f"{base_url}/api/sessions/{session_id}/start",
                        headers={"X-API-Key": api_key},
                        timeout=15
                    )
                    if start_r.status_code in (200, 201):
                        start_data = start_r.json()
                        new_status = start_data.get("status", "")
                        # SUCCESS: reset failure streak (not increment!)
                        _reset_failure_streak()
                        frappe.logger().info(
                            f"OpenWA session restarted: {status} -> {new_status}. "
                            f"If {new_status} != 'connected', QR scan may be needed."
                        )
                        return {"status": "started", "from": status, "to": new_status, "checked": now()}
                    else:
                        _increment_failure_streak()
                        frappe.log_error(
                            title="OpenWA Session Start Failed",
                            message=f"Start returned {start_r.status_code}: {start_r.text[:200]}",
                        )
                        return {"status": "error", "reason": f"Start returned {start_r.status_code}"}
                except Exception as start_e:
                    _increment_failure_streak()
                    frappe.log_error(title="OpenWA Session Start Error", message=str(start_e))
                    return {"status": "error", "reason": f"Start error: {start_e}"}

            healthy_statuses = ["ready", "connected", "qr_ready"]
            if status not in healthy_statuses:
                _increment_failure_streak()
                frappe.log_error(
                    title="OpenWA Session Unhealthy",
                    message=f"Session status: {status}. No auto-recovery available for this state.",
                )
                return {"status": "error", "reason": f"Session status: {status}"}

            # Check for stale session (lastActive > STALE_THRESHOLD_MINUTES ago)
            if _session_is_stale(settings, data):
                # Multi-site collision guard before stop/start
                if _check_multi_site_collision(settings):
                    return {"status": "guarded", "reason": "Multi-site collision - stale recovery skipped", "checked": now()}

                # Auto-recover: stop/start session to re-establish WebSocket
                result = _restart_session(settings)
                if result.get("status") == "recovered":
                    # Retry unsent messages now that session is back
                    _retry_unsent()
                    _reset_failure_streak()
                    return {
                        "status": "recovered",
                        "session": status,
                        "lastActive": result.get("lastActive"),
                        "checked": now(),
                    }
                # Recovery failed — log and keep stale flag set
                last_active = data.get("lastActive", "unknown")
                frappe.log_error(
                    title="OpenWA Session Stale",
                    message=(
                        f"Session {settings.session_id} "
                        f"lastActive={last_active}. "
                        f"Auto-recovery failed: "
                        f"{result.get('reason', 'unknown')}. "
                        f"Phone may be offline. Scan QR code at {settings.base_url}/ to reconnect."
                    ),
                )
                return {"status": "stale", "session": status, "lastActive": data.get("lastActive")}

            # Session is healthy — reset failure streak
            _reset_failure_streak()

        except Exception as e:
            _increment_failure_streak()
            _reset_404_streak()
            frappe.log_error(
                title="OpenWA Health Check Failed",
                message=f"Session check failed: {e}. Restart manually if persistent.",
            )
            return {"status": "error", "reason": f"Session check failed: {e}"}
    # 3. Retry any missed notifications now that session is confirmed healthy
        _retry_unsent()

        return {"status": "healthy", "session": status, "checked": now()}

    except Exception as e:
        _increment_failure_streak()
        frappe.log_error(f"OpenWA health check error: {e}", "OpenWA Health Check")
        return {"status": "error", "reason": str(e)}


def check_inbound_webhook_health():
    """Scheduled job: verify inbound webhook is reachable."""
    try:
        settings = frappe.get_cached_doc("OpenWA Settings")
        if not settings.webhook_enabled:
            return {"status": "skipped", "reason": "OpenWA not enabled"}

        site_url = settings.get("inbound_webhook_url", "") or frappe.utils.get_url()
        if not site_url:
            return {"status": "error", "reason": "No site URL configured"}

        import requests as _requests
        test_url = site_url.rstrip("/") + "/api/method/kreativ_notification.notification.inbound.receive_whatsapp_message"
        r = _requests.post(test_url, json={}, timeout=10)
        if r.status_code in (200, 400, 401, 403):
            return {"status": "healthy", "http_code": r.status_code}
        return {"status": "error", "reason": f"HTTP {r.status_code}"}
    except Exception as e:
        frappe.log_error(title="Inbound Webhook Health Check", message=str(e))
        return {"status": "error", "reason": str(e)}