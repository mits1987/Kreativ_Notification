# Copyright (c) 2026, Kreativ Gravures
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document


class NotificationRule(Document):
    def validate(self):
        self._validate_condition()
        self._validate_template_doctype()

    def _validate_condition(self):
        if not self.condition:
            return
        try:
            frappe.safe_eval(self.condition, eval_locals={"doc": frappe._dict()})
        except AttributeError:
            pass  # empty dict lacks fields — syntax was still parsed fine
        except SyntaxError as e:
            frappe.throw(_("Condition has a syntax error: {0}").format(e))
        except Exception:
            pass  # runtime errors against an empty doc are expected

    def _validate_template_doctype(self):
        tpl_dt = frappe.db.get_value("Message Template", self.message_template, "for_doctype")
        if tpl_dt and tpl_dt != self.document_type:
            frappe.throw(_("Template {0} is for {1}, but this rule is on {2}.")
                         .format(self.message_template, tpl_dt, self.document_type))

    # ------------------------------------------------------------------
    # Condition evaluation
    # ------------------------------------------------------------------

    def applies_to(self, doc) -> bool:
        if not self.condition:
            return True
        try:
            return bool(frappe.safe_eval(
                self.condition,
                eval_locals={"doc": doc, "frappe": frappe._dict(utils=frappe.utils)},
            ))
        except Exception:
            frappe.log_error(
                title=f"Notification Rule condition failed: {self.name}",
                message=frappe.get_traceback(),
            )
            return False

    # ------------------------------------------------------------------
    # Recipient resolution → list of raw addresses (numbers / emails)
    # ------------------------------------------------------------------

    def resolve_recipients(self, doc) -> list[str]:
        out: list[str] = []
        for row in self.recipients:
            try:
                if row.recipient_type == "Static":
                    out.append(row.value)

                elif row.recipient_type == "Document Field":
                    val = doc.get(row.value)
                    if val:
                        out.append(val)

                elif row.recipient_type == "Role":
                    out.extend(self._role_numbers(row))

                elif row.recipient_type == "Linked Contact":
                    val = self._linked_contact_number(doc, row)
                    if val:
                        out.append(val)
            except Exception:
                frappe.log_error(
                    title=f"Recipient resolution failed: rule {self.name}",
                    message=frappe.get_traceback(),
                )

        # de-dupe, preserve order
        seen, unique = set(), []
        for r in out:
            r = (r or "").strip()
            if r and r not in seen:
                seen.add(r)
                unique.append(r)
        return unique

    def _role_numbers(self, row) -> list[str]:
        users = frappe.get_all("Has Role",
                               filters={"role": row.value, "parenttype": "User"},
                               pluck="parent")
        if not users:
            return []
        field = row.contact_fieldname or "mobile_no"
        rows = frappe.get_all("User",
                              filters={"name": ["in", users], "enabled": 1},
                              fields=["name", field])
        return [r.get(field) for r in rows if r.get(field)]

    def _linked_contact_number(self, doc, row) -> str | None:
        """row.value is a Link fieldname on the doc (customer / supplier /
        employee). Pull the contact field from the linked party."""
        party_name = doc.get(row.value)
        if not party_name:
            return None

        link_dt = None
        df = doc.meta.get_field(row.value)
        if df and df.fieldtype == "Link":
            link_dt = df.options
        if not link_dt:
            return None

        field = row.contact_fieldname or "mobile_no"

        # Direct field on the linked doc (Employee.cell_number etc.)
        if frappe.get_meta(link_dt).has_field(field):
            val = frappe.db.get_value(link_dt, party_name, field)
            if val:
                return val

        # Fall back to the party's primary Contact (Customer/Supplier)
        contact = frappe.db.get_value(
            "Dynamic Link",
            {"link_doctype": link_dt, "link_name": party_name,
             "parenttype": "Contact"},
            "parent",
        )
        if contact:
            return frappe.db.get_value("Contact", contact, field) \
                or frappe.db.get_value("Contact", contact, "mobile_no")
        return None

    def get_recipient_language(self, doc, recipient_row=None) -> str | None:
        """Best-effort language for variant selection (party language, else None)."""
        for lf in ("language", "print_language"):
            if doc.meta.has_field(lf) and doc.get(lf):
                return doc.get(lf)
        return None
