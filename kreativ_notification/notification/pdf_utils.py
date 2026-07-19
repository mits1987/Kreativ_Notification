"""PDF generation utilities."""
import frappe
import base64


def generate_pdf_bytes(doctype: str, name: str, print_format: str = None) -> bytes:
    """Generate PDF bytes for a document using headless Chromium."""
    pdf_bytes = frappe.get_print(
        doctype, name,
        print_format=print_format or None,
        as_pdf=True,
    )
    if isinstance(pdf_bytes, str):
        return base64.b64decode(pdf_bytes)
    return pdf_bytes


def _rewrite_image_src_for_pdf(html: str) -> str:
    """Rewrite /files/... image URLs to base64 data URIs for headless Chromium."""
    from bs4 import BeautifulSoup
    import requests

    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src.startswith("/files/"):
            try:
                file_url = frappe.utils.get_url() + src
                resp = requests.get(file_url, timeout=10)
                if resp.ok:
                    import mimetypes
                    mime = mimetypes.guess_type(src)[0] or "image/png"
                    b64 = base64.b64encode(resp.content).decode("utf-8")
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