frappe.ui.form.on('OpenWA Settings', {
	refresh(frm) {
		frm.add_custom_button(__('Refresh Status & QR'), function() {
			frappe.call({
				method: 'kreativ_notification.kreativ_notification.doctype.openwa_settings.openwa_settings.get_session_status',
				callback: function(r) {
					if (r.message) {
						frm.set_value('session_status', r.message.status);
						frm.set_value('session_phone', r.message.phone);
						frm.set_value('session_pushname', r.message.pushname);
						frappe.show_alert({message: __('Status updated: ') + r.message.status, indicator: 'green'});
					}
				}
			});
		}).addClass('btn-primary');

		frm.add_custom_button(__('Start Session'), function() {
			frappe.call({
				method: 'kreativ_notification.kreativ_notification.doctype.openwa_settings.openwa_settings.start_session',
				callback: function(r) {
					if (r.message) {
						frappe.show_alert({message: r.message.message, indicator: r.message.status === 'ok' ? 'green' : 'red'});
						frm.reload();
					}
				}
			});
		}).addClass('btn-secondary');

		frm.add_custom_button(__('Stop Session'), function() {
			frappe.call({
				method: 'kreativ_notification.kreativ_notification.doctype.openwa_settings.openwa_settings.stop_session',
				callback: function(r) {
					if (r.message) {
						frappe.show_alert({message: r.message.message, indicator: r.message.status === 'ok' ? 'green' : 'red'});
						frm.reload();
					}
				}
			});
		}).addClass('btn-secondary');

		frm.add_custom_button(__('Create New Session'), function() {
			frappe.confirm(__('This will create a new session and invalidate the current one. Continue?'), function() {
				frappe.call({
					method: 'kreativ_notification.kreativ_notification.doctype.openwa_settings.openwa_settings.create_new_session',
					callback: function(r) {
						if (r.message) {
							frappe.show_alert({message: r.message.message, indicator: r.message.status === 'ok' ? 'green' : 'red'});
							frm.reload();
						}
					}
				});
			});
		}).addClass('btn-danger');

		frm.add_custom_button(__('Get QR Code'), function() {
			frappe.call({
				method: 'kreativ_notification.kreativ_notification.doctype.openwa_settings.openwa_settings.get_session_qr',
				callback: function(r) {
					if (r.message && r.message.status === 'ok') {
						frm.set_value('session_status', r.message.session_status);
						const qr_html = '<div style="text-align:center;"><img src="' + r.message.qr + '" style="max-width:100%;height:auto;border:1px solid #ddd;border-radius:4px;padding:10px;background:#fff;" /></div>';
						if (frm.fields_dict.session_qr && frm.fields_dict.session_qr.$wrapper) {
							frm.fields_dict.session_qr.$wrapper.html(qr_html);
						}
						frappe.show_alert({message: __('QR Code loaded. Scan with WhatsApp.'), indicator: 'blue'});
					} else if (r.message) {
						frappe.msgprint(r.message.message);
					}
				}
			});
		}, __('Actions'));
	}
});