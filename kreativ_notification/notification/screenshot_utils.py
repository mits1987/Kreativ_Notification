"""Playwright screenshot utilities."""
import frappe


def screenshot_html_playwright(html: str, width: int = 1000, height: int = None) -> bytes:
    """Render HTML to JPEG using headless Chromium via Playwright."""
    from playwright.sync_api import sync_playwright

    if height is None:
        # Estimate height based on content length
        height = max(800, len(html) // 2)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": height})
        page.set_content(html, wait_until="networkidle")
        jpg = page.screenshot(full_page=True, type="jpeg", quality=85)
        browser.close()
        return jpg