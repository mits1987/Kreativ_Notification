/* kreativ_notification.js - Desktop JS for Notification Queue and WhatsApp features */

window.kreativ_notification = window.kreativ_notification || {};

kreativ_notification.setup = function() {
    // Add notification queue link to desk if user has permission
    if (frappe.boot && frappe.boot.user && frappe.boot.user.roles) {
        const roles = frappe.boot.user.roles;
        const hasWhatsAppAccess = roles.includes("WhatsApp User") || roles.includes("WhatsApp Manager");
        
        if (hasWhatsAppAccess && !frappe.boot.desk_menu.includes("Notification Queue")) {
            // The doctype will appear automatically if user has read permission
        }
    }
};

// Initialize on desk load
if (typeof frappe !== "undefined" && frappe.ready) {
    frappe.ready(kreativ_notification.setup);
}