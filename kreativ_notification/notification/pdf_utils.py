"""PDF generation utilities."""
import frappe
import base64
import subprocess
import tempfile
import os
import shutil


def _chrome_path(channel_name: str = None) -> str:
    """Return path to Chrome/Chromium binary.

    Priority:
    1. webhook_secret field on Notification Channel (if channel_name provided)
    2. chrome_path in site_config.json / common_site_config.json
    3. known binary names on PATH
    """
    # Check Notification Channel for chromium_path
    if channel_name:
        channel_path = frappe.db.get_value("Notification Channel", channel_name, "chromium_path")
        if channel_path and os.path.exists(channel_path):
            return channel_path

    # Check site config
    configured = frappe.conf.get("chrome_path")
    if configured and os.path.exists(configured):
        return configured

    # Search PATH
    for binary in ("chromium", "chromium-browser", "google-chrome",
                   "google-chrome-stable", "chrome", "headless_shell"):
        path = shutil.which(binary)
        if path:
            return path

    frappe.throw(
        "No Chromium binary found. Set 'chromium_path' on Notification Channel "
        "or 'chrome_path' in site_config.json, "
        'e.g. "chrome_path": "/home/mitesh/frappe-bench-v16/chromium/chrome-linux/headless_shell"'
    )


def generate_pdf_bytes(doctype: str, name: str, print_format: str = None, channel_name: str = None) -> bytes:
    """Generate PDF bytes for a document using headless Chromium.

    Rewrites /files/ image URLs to base64 data URIs and strips action banner
    before sending HTML to Chromium for PDF generation.
    """
    # Get HTML first (not PDF) so we can rewrite image URLs
    html = frappe.get_print(
        doctype, name,
        print_format=print_format or None,
        as_pdf=False,
    )

    # Rewrite /files/... image URLs to base64 data URIs
    html = _rewrite_image_src_for_pdf(html)

    # Strip action banner (print toolbar)
    html = _strip_action_banner(html)

    # Generate PDF using headless Chromium
    return _generate_pdf_from_html(html, channel_name)


def generate_pdf_from_html(html: str, channel_name: str = None) -> bytes:
    """Generate PDF from HTML string using headless Chromium.

    Applies image rewrite and banner stripping for consistency.
    """
    # Rewrite /files/... image URLs to base64 data URIs
    html = _rewrite_image_src_for_pdf(html)

    # Strip action banner (print toolbar)
    html = _strip_action_banner(html)

    # Generate PDF using headless Chromium
    return _generate_pdf_from_html(html, channel_name)


def _generate_pdf_from_html(html: str, channel_name: str = None) -> bytes:
    """Internal: generate PDF from HTML using headless Chromium."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        html_path = f.name

    pdf_path = html_path.replace(".html", ".pdf")
    try:
        chrome = _chrome_path(channel_name)
        result = subprocess.run(
            [
                chrome,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--print-to-pdf=" + pdf_path,
                f"file://{html_path}",
            ],
            timeout=30,
            capture_output=True,
        )
        if result.returncode != 0:
            raise Exception(f"Chromium PDF failed: {result.stderr.decode()}")

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        return pdf_bytes
    finally:
        for p in (html_path, pdf_path):
            try:
                os.unlink(p)
            except Exception:
                pass


def _rewrite_image_src_for_pdf(html: str) -> str:
    """Rewrite /files/... image URLs to base64 data URIs by reading from filesystem."""
    from bs4 import BeautifulSoup
    import os
    import mimetypes

    soup = BeautifulSoup(html, "html.parser")
    files_path = frappe.utils.get_files_path()

    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src.startswith("/files/"):
            # Extract filename from /files/FILENAME
            filename = src.split("/files/", 1)[1].split("?")[0]
            file_path = os.path.join(files_path, filename)
            try:
                if os.path.exists(file_path):
                    with open(file_path, "rb") as f:
                        content = f.read()
                    mime = mimetypes.guess_type(filename)[0] or "image/png"
                    b64 = base64.b64encode(content).decode("utf-8")
                    img["src"] = f"data:{mime};base64,{b64}"
            except Exception:
                pass
    return str(soup)


def _strip_action_banner(html: str) -> str:
    """Remove the action-banner div (print toolbar) from PDF output."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for banner in soup.find_all("div", class_="action-banner"):
        banner.decompose()
    return str(soup)


def screenshot_html(html_content: str, width: int = 1000, max_height: int = 8000) -> bytes:
    """Render HTML to full-page PNG via Chromium headless, return raw PNG bytes.

    Fixes:
    - Caps max_height at 8000px (was 20000px causing OOM)
    - Removed --force-device-scale-factor=2 (was 2x memory)
    - Reuses _chrome_path() for portable binary detection
    """
    from PIL import Image, ImageChops
    import io

    # Clean HTML for screenshot
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")
    for banner in soup.find_all("div", class_="action-banner"):
        banner.decompose()
    clean_html = str(soup)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
        f.write(clean_html)
        html_path = f.name
    fd, png_path = tempfile.mkstemp(suffix='.png')
    os.close(fd)

    try:
        # Cap height at 8000px max to prevent OOM (was 20000)
        # Removed --force-device-scale-factor=2 to halve memory usage
        cmd = [
            _chrome_path(), '--headless', '--no-sandbox', '--disable-gpu',
            '--hide-scrollbars',
            f'--window-size={width},{min(max_height, 8000)}',
            '--screenshot=' + png_path, html_path
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
        img = Image.open(png_path)

        # ---- Smart crop: detect background from corners ----
        w, h = img.size
        corner_sample = 10  # pixels from corner
        corners = [
            img.crop((0, 0, corner_sample, corner_sample)),           # top-left
            img.crop((w - corner_sample, 0, w, corner_sample)),        # top-right
            img.crop((0, h - corner_sample, corner_sample, h)),        # bottom-left
            img.crop((w - corner_sample, h - corner_sample, w, h)),    # bottom-right
        ]
        # Find most common color among corners (the background)
        from collections import Counter
        corner_colors = []
        for c in corners:
            colors = c.getcolors(corner_sample * corner_sample)
            if colors:
                corner_colors.append(max(colors, key=lambda x: x[0])[1])
        if corner_colors:
            bg_color = Counter(corner_colors).most_common(1)[0][0]
            # If BG is RGBA, use RGB for comparison
            if isinstance(bg_color, tuple) and len(bg_color) == 4:
                bg_color = bg_color[:3]

            # Convert to RGB for comparison
            if img.mode != 'RGB':
                img_rgb = img.convert('RGB')
            else:
                img_rgb = img

            # Use ImageChops.difference to create mask of non-background pixels
            bg_image = Image.new('RGB', img_rgb.size, bg_color)
            diff = ImageChops.difference(img_rgb, bg_image)
            bbox = diff.getbbox()
            if bbox:
                # Add 15px padding at bottom
                left, top, right, bottom = bbox
                bottom = min(bottom + 15, h)
                img = img.crop((0, 0, w, bottom))

        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    finally:
        os.unlink(html_path)
        if os.path.exists(png_path):
            os.unlink(png_path)