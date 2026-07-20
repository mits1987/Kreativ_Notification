# Copyright (c) 2026, Kreativ Gravures
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document


class MessageTemplate(Document):
    def validate(self):
        langs = [v.language for v in self.variants]
        if len(langs) != len(set(langs)):
            frappe.throw(_("Duplicate language variants are not allowed."))
        if self.default_language and self.default_language not in langs:
            frappe.throw(_("Add a variant for the Default Language ({0}).")
                         .format(self.default_language))
        # Fail fast on broken Jinja instead of at send time
        for v in self.variants:
            try:
                frappe.render_template(v.body, {"doc": frappe._dict(), "frappe": frappe})
            except Exception as e:
                frappe.throw(_("Variant '{0}' has invalid Jinja: {1}").format(v.language, e))

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def get_variant(self, language: str | None):
        by_lang = {v.language: v for v in self.variants}
        return (by_lang.get(language)
                or by_lang.get(self.default_language)
                or (self.variants[0] if self.variants else None))

    def render(self, doc, language: str | None = None) -> dict:
        """Render body/subject for a document.

        Returns {"body": str, "subject": str, "language": str}
        """
        variant = self.get_variant(language)
        if not variant:
            frappe.throw(_("Template {0} has no variants.").format(self.name))

        context = {"doc": doc, "frappe": frappe, "utils": frappe.utils}
        body = frappe.render_template(variant.body, context)
        subject = frappe.render_template(variant.subject or "", context)
        return {"body": body, "subject": subject, "language": variant.language}

    def render_attachment_filename(self, doc) -> str:
        if self.attachment_filename:
            try:
                name = frappe.render_template(self.attachment_filename, {"doc": doc})
                if name:
                    return name if name.lower().endswith(".pdf") else f"{name}.pdf"
            except Exception:
                pass
        return f"{doc.name}.pdf"


@frappe.whitelist()
def preview(template: str, docname: str = None, language: str = None) -> dict:
    """Preview a rendered template against a real (or the latest) document."""
    frappe.only_for(("System Manager", "WhatsApp Manager"))
    tpl = frappe.get_doc("Message Template", template)
    if not tpl.for_doctype:
        frappe.throw(_("Set 'For DocType' on the template to preview."))

    if not docname:
        latest = frappe.get_all(tpl.for_doctype, order_by="modified desc",
                                pluck="name", limit_page_length=1)
        if not latest:
            frappe.throw(_("No {0} documents exist to preview against.")
                         .format(tpl.for_doctype))
        docname = latest[0]

    doc = frappe.get_doc(tpl.for_doctype, docname)
    if not doc.has_permission("read"):
        frappe.throw(_("Not permitted to read {0} {1}").format(tpl.for_doctype, docname),
                     frappe.PermissionError)
    rendered = tpl.render(doc, language)
    rendered["docname"] = docname
    return rendered
