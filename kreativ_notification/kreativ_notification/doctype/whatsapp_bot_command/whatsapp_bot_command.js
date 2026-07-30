frappe.ui.form.on('WhatsApp Bot Command', {
	setup: function(frm) {
		// Set print_format query for child table rows
		frm.set_query('print_format', 'bot_commands', function(doc, cdt, cdn) {
			let row = locals[cdt][cdn];
			if (row && row.doc_type) {
				return {
					filters: {
						doc_type: row.doc_type
					}
				};
			}
		});
	},
	doc_type: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row && row.doc_type) {
			// Force refresh the print_format field to apply new filter
			frm.fields_dict.bot_commands.grid.grid_rows_by_docname[cdn].refresh_field('print_format');
		}
	}
});