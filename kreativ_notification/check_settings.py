import frappe

def execute():
    frappe.connect('kreativ316')
    doc = frappe.get_doc('OpenWA Settings')
    for k,v in doc.as_dict().items():
        if v and k not in ('name','owner','modified_by','creation','modified','docstatus','idx','doctype','modified_from'):
            if 'key' in k.lower() or 'secret' in k.lower() or 'token' in k.lower() or 'password' in k.lower():
                print(f'{k}: ***{str(v)[-4:]}')
            else:
                print(f'{k}: {v}')
    frappe.destroy()
