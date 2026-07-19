/* print_whatsapp.js - Print Preview WhatsApp Button

Injects a "Send to WhatsApp" button on the Print Preview page (/desk#print/...)
Role-restricted to "WhatsApp User" or "WhatsApp Manager"
*/

window.kreativ_notification = window.kreativ_notification || {};

kreativ_notification.inject_whatsapp_button = function() {
    // Check if user has WhatsApp role
    const hasWhatsAppRole = frappe.boot.user.roles.some(r => 
        r === "WhatsApp User" || r === "WhatsApp Manager"
    );
    
    if (!hasWhatsAppRole) return;

    // Check if we're on a print preview page
    const isPrintPreview = window.location.pathname.startsWith("/desk#print/");
    if (!isPrintPreview) return;

    // Check if button already exists
    if (document.getElementById("whatsapp-print-btn")) return;

    // Find the print toolbar or action area
    const toolbar = document.querySelector(".print-toolbar, .print-actions, .page-header, .actions");
    if (!toolbar) return;

    // Extract doctype and name from URL
    const path = window.location.pathname;
    const match = path.match(/\/desk#print\/([^\/]+)\/([^\/]+)/);
    if (!match) return;

    const doctype = decodeURIComponent(match[1]);
    const name = decodeURIComponent(match[2]);

    // Create WhatsApp button
    const btn = document.createElement("button");
    btn.id = "whatsapp-print-btn";
    btn.className = "btn btn-primary btn-sm";
    btn.style.marginLeft = "8px";
    btn.innerHTML = '<i class="fa fa-whatsapp"></i> WhatsApp';
    btn.title = "Send this document to WhatsApp";

    btn.addEventListener("click", function() {
        frappe.call({
            method: "kreativ_notification.notification.send.send_document_via_whatsapp",
            args: {
                doctype: doctype,
                name: name,
                print_format: "Standard",
                chat_id_override: null
            },
            callback: function(r) {
                if (r.message && r.message.status === "queued") {
                    frappe.show_alert({message: "WhatsApp send queued!", indicator: "green"});
                } else if (r.message && r.message.error) {
                    frappe.msgprint({title: "Error", message: r.message.error, indicator: "red"});
                }
            }
        });
    });

    toolbar.appendChild(btn);
};

// Multiple injection strategies for reliability
function tryInject() {
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", tryInject);
        return;
    }
    kreativ_notification.inject_whatsapp_button();
}

// Strategy 1: DOM ready
if (typeof document !== "undefined") {
    tryInject();
}

// Strategy 2: Frappe router change (for SPA navigation)
if (typeof frappe !== "undefined" && frappe.router) {
    frappe.router.on("change", function() {
        setTimeout(kreativ_notification.inject_whatsapp_button, 100);
    });
}

// Strategy 3: MutationObserver for dynamic content
if (typeof MutationObserver !== "undefined") {
    const observer = new MutationObserver(function(mutations) {
        for (const mutation of mutations) {
            if (mutation.addedNodes.length > 0) {
                kreativ_notification.inject_whatsapp_button();
                break;
            }
        }
    });
    observer.observe(document.body, { childList: true, subtree: true });
}