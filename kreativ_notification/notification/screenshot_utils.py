"""Playwright screenshot utilities."""
import frappe
from playwright.sync_api import sync_playwright


def screenshot_html_playwright(html: str, width: int = 1000, height: int = None) -> bytes:
    """Render HTML to PNG using headless Chromium via Playwright."""
    if height is None:
        # Estimate height based on content length
        height = max(800, len(html) // 2)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": height})
        page.set_content(html, wait_until="networkidle")
        png = page.screenshot(full_page=True, type="png")
        browser.close()
        return png