// Adds a "Send to WhatsApp" button to the Print Preview toolbar.
// Opens a clean contact picker modal to select recipient,
// then generates and sends the PDF via WhatsApp.

(function () {
    "use strict";

    // console.log("[print_whatsapp_v3] IIFE started, current route:", frappe.get_route ? frappe.get_route() : "no route", "hash:", window.location.hash);
    // console.log("[print_whatsapp_v3] frappe ready:", window.frappe && frappe.router && frappe.get_route);

    const WA_ICON = '<svg viewBox="0 0 448 512" width="16" height="16" fill="white" style="vertical-align: top; margin: 2px 0 0 0;"><path d="M380.9 97.1C339 55.1 283.2 32 223.9 32c-122.4 0-222 99.6-222 222 0 39.1 10.2 77.3 29.6 111L0 480l117.7-30.9c32.4 17.7 68.9 27 106.1 27h.1c122.3 0 224.1-99.6 224.1-222 0-59.3-25.2-115-67.1-157zm-157 341.6c-33.2 0-65.7-8.9-94-25.7l-6.7-4-69.8 18.3L72 359.2l-4.4-7c-18.5-29.4-28.2-63.3-28.2-98.2 0-101.7 82.8-184.5 184.6-184.5 49.3 0 95.6 19.2 130.4 54.1 34.8 34.9 56.2 81.2 56.1 130.5 0 101.8-84.9 184.6-186.6 184.6zm101.2-138.2c-5.5-2.8-32.8-16.2-37.9-18-5.1-1.9-8.8-2.8-12.5 2.8-3.7 5.6-14.3 18-17.6 21.8-3.2 3.7-6.5 4.2-12 1.4-32.6-16.3-54-29.1-75.5-66-5.7-9.8 5.7-9.1 16.3-30.3 1.8-3.7.9-6.9-.5-9.7-1.4-2.8-12.5-30.1-17.1-41.2-4.5-10.8-9.1-9.3-12.5-9.5-3.2-.2-6.9-.2-10.6-.2-3.7 0-9.7 1.4-14.8 6.9-5.1 5.6-19.4 19-19.4 46.3 0 27.3 19.9 53.7 22.6 57.4 2.8 3.7 39.1 59.7 94.8 83.8 35.2 15.2 49 16.5 66.6 13.9 10.7-1.6 32.8-13.4 37.4-26.4 4.6-13 4.6-24.1 3.2-26.4-1.3-2.5-5-3.9-10.5-6.6z"/></svg>';

    /* -----------------------------------------------------------
       Contact Picker Modal
    ----------------------------------------------------------- */
    function showContactPicker(doctype, name, print_format, _lang) {
        var d = new frappe.ui.Dialog({
            title: "Send PDF to WhatsApp",
            size: "large",
            fields: [
                {
                    fieldname: "manual_send_html",
                    fieldtype: "HTML",
                    options: getManualSendHtml(),
                },
                {
                    fieldtype: "Section Break",
                    label: "Or search contacts",
                },
                {
                    fieldname: "search_input",
                    fieldtype: "Data",
                    label: "",
                    placeholder: "Search contacts or groups...",
                    onchange: function () {
                        filterPickerList(d);
                    },
                },
                {
                    fieldname: "picker_html",
                    fieldtype: "HTML",
                },
            ],
            primary_action_label: "Cancel",
            primary_action: function () {
                d.hide();
            },
        });
        // Store context in dialog for filter/search to use - BEFORE d.show()
        d._wa_context = { doctype: doctype, name: name, print_format: print_format, _lang: _lang };

        d.show();

        // Style
        d.$wrapper.find(".modal-dialog").css({
            "max-width": "480px"
        });
        d.$wrapper.find(".modal-content").css({
            "border-radius": "12px",
            border: "none",
            "box-shadow": "0 8px 32px rgba(0,0,0,0.15)"
        });
        d.$wrapper.find(".modal-header").css({
            "border-bottom": "1px solid #f0f0f0",
            padding: "16px 20px"
        });
        d.$wrapper.find(".modal-header .modal-title").css({
            "font-size": "15px",
            "font-weight": "600"
        });
        d.$wrapper.find(".modal-body").css({
            padding: "16px 20px"
        });
        d.$wrapper.find(".modal-footer").css({
            "border-top": "1px solid #f0f0f0",
            padding: "12px 20px"
        });

        // Style search field
        setTimeout(function () {
            var $searchInput = d.$wrapper.find('[data-fieldname="search_input"] input');
            var $controlWrap = $searchInput.closest('.frappe-control');

            $searchInput.css({
                "border-radius": "8px",
                padding: "10px 14px",
                "font-size": "14px",
                background: "#fff",
                border: "1px solid #d0d5dd",
                outline: "none",
                "box-sizing": "border-box",
                flex: "1",
                "min-width": "0",
                transition: "all 0.2s",
                "font-family": "inherit",
                "-webkit-appearance": "none"
            });
            $searchInput.on("focus", function () {
                $(this).css({
                    "border-color": "#25D366",
                    "box-shadow": "0 0 0 3px rgba(37,211,102,0.1)"
                });
            });
            $searchInput.on("blur", function () {
                $(this).css({
                    "border-color": "#d0d5dd",
                    "box-shadow": "none"
                });
            });
            // Real-time server search on keystroke (debounced 300ms)
            $searchInput.on("input", function () {
                debouncedServerSearch(d);
            });

            // Find the control-input-wrapper which wraps the actual input - this is where we want the button
            var $inputWrapper = $searchInput.closest('.control-input-wrapper');

            // Ensure parent Frappe control wrapper takes full width (match manual send field width)
            $inputWrapper.closest('.frappe-control').css('width', '100%');
            $inputWrapper.closest('.control-input').css('width', '100%');

            // Make the input wrapper a flex container matching manual send layout (gap + stretch)
            $inputWrapper.css({
                'display': 'flex',
                'align-items': 'stretch',
                'width': '100%',
                'gap': '8px'
            });

            // Add search icon button as fallback (triggers server search)
            var $searchBtn = $('<button type="button" aria-label="Search contacts" class="wa-search-btn" style="'
                + 'padding:10px 20px;background:#25D366;color:white;border:none;'
                + 'border-radius:8px;cursor:pointer;display:flex;align-items:center;'
                + 'justify-content:center;transition:all 0.15s;'
                + 'font-size:14px;font-weight:600;flex-shrink:0;box-sizing:border-box;'
                + '-webkit-appearance:none;line-height:1;">'
                + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"'
                + ' stroke-linecap="round" stroke-linejoin="round">'
                + '<circle cx="11" cy="11" r="8"></circle>'
                + '<line x1="21" y1="21" x2="16.65" y2="16.65"></line>'
                + '</svg>'
                + '</button>');
            $searchBtn.hover(
                function () { $(this).css("background", "#128C7E"); },
                function () { $(this).css("background", "#25D366"); }
            );
            $searchBtn.on("click", function () {
                searchContactsServerSide(d);
            });
            // Append button INSIDE the control-input-wrapper (next to input)
            $inputWrapper.append($searchBtn);
        }, 50);

        // Wire up the manual send
        setTimeout(function () {
            var $row = d.$wrapper.find('[data-fieldname="manual_send_html"]');
            var $input = $row.find(".wa-manual-input");
            var $btn = $row.find(".wa-manual-btn");

            function doSend() {
                var num = $input.val() || "";
                var clean = num.replace(/[^0-9]/g, "");
                if (!clean || clean.length < 10) {
                    frappe.msgprint("Enter a valid phone with country code (e.g. 919106526195)");
                    $input.focus();
                    return;
                }

                $btn.prop("disabled", true).html("Validating...");

                // Server-side validation via phonenumbers library
                frappe.call({
                    method: "gravures_custom.overrides.validate_phone_number",
                    args: { phone: clean },
                    callback: function (r) {
                        $btn.prop("disabled", false).html("Send");
                        if (!r.message || !r.message.valid) {
                            frappe.msgprint({
                                title: "Invalid Phone Number",
                                message: r.message?.error || "Could not validate phone number",
                                indicator: "red"
                            });
                            $input.focus();
                            return;
                        }

                        var result = r.message;
                        var formatted = result.formatted || result.e164 || clean;

                        // Confirm before sending
                        frappe.confirm(
                            "Send PDF to <b>" + frappe.utils.escape_html(formatted) + "</b> via WhatsApp?",
                            function () {
                                d.hide();
                                var ctx = d._wa_context || {};
                                sendWithSelectedChat(ctx.doctype, ctx.name, ctx.print_format, ctx._lang, result.chat_id, formatted);
                            },
                            function () {
                                // User cancelled - re-focus input
                                $input.focus();
                            }
                        );
                    },
                    error: function () {
                        $btn.prop("disabled", false).html("Send");
                        frappe.msgprint("Validation failed. Please try again.");
                        $input.focus();
                    }
                });
            }

            $btn.on("click", doSend);
            $input.on("keydown", function (e) {
                if (e.which === 13) { doSend(); }
            });
            $input.on("focus", function () {
                $(this).css({
                    "border-color": "#25D366",
                    "box-shadow": "0 0 0 3px rgba(37,211,102,0.1)"
                });
            });
            $input.on("blur", function () {
                $(this).css({
                    "border-color": "#d0d5dd",
                    "box-shadow": "none"
                });
            });
            // Focus input
            setTimeout(function () { $input.focus(); }, 200);
        }, 50);

        // Show loading state
        d.fields_dict.picker_html.$wrapper.html(
            '<div style="text-align:center;padding:30px;color:#999;font-size:13px;">Loading chats...</div>'
        );

        // Fetch chats from backend
        frappe.call({
            method: "gravures_custom.overrides.get_whatsapp_chats",
            callback: function (r) {
                if (!r.message) {
                    d.fields_dict.picker_html.$wrapper.html(
                        '<div style="text-align:center;padding:30px;color:#e74c3c;">Failed to load chats.</div>'
                    );
                    return;
                }
                d._picker_data = r.message;
                var ctx = d._wa_context || {};
                renderPickerList(d, "", ctx.doctype, ctx.name, ctx.print_format, ctx._lang);
            },
            error: function () {
                d.fields_dict.picker_html.$wrapper.html(
                    '<div style="text-align:center;padding:30px;color:#e74c3c;">Error loading chats.</div>'
                );
            },
        });
    }

    // Debounced server search - wait 300ms after last keystroke
    var searchDebounceTimer = null;
    function debouncedServerSearch(d) {
        if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
        searchDebounceTimer = setTimeout(function () {
            searchContactsServerSide(d);
        }, 300);
    }

    function getManualSendHtml() {
        return (
            '<div style="margin: 0 0 4px 0;">' +
                '<label style="display:block;font-size:12px;color:#555;font-weight:500;margin-bottom:6px;">Phone Number</label>' +
                '<div style="display:flex;gap:8px;align-items:stretch;flex-wrap:nowrap;">' +
                    '<input type="tel" inputmode="numeric" pattern="[0-9]*" placeholder="e.g. 919106526195" class="wa-manual-input" style="' +
                        'flex:1;min-width:0;padding:10px 14px;font-size:14px;border:1px solid #d0d5dd;' +
                        'border-radius:8px;outline:none;transition:all 0.2s;background:#fff;color:#1a1a1a;' +
                        'font-family:inherit;-webkit-appearance:none;"' +
                        ' autocomplete="tel" />' +
                    '<button class="wa-manual-btn" style="' +
                        'padding:10px 20px;background:#25D366;color:white;border:none;border-radius:8px;' +
                        'font-size:14px;font-weight:600;cursor:pointer;transition:all 0.15s;' +
                        'white-space:nowrap;flex-shrink:0;line-height:1;-webkit-appearance:none;"' +
                        '>Send</button>' +
                '</div>' +
                '<p style="margin:4px 0 0;font-size:11px;color:#999;">Include country code, no + or spaces</p>' +
            '</div>'
        );
    }

    /* -----------------------------------------------------------
       Contact list rendering
    ----------------------------------------------------------- */
    function renderPickerList(d, query, doctype, name, print_format, _lang) {
        var data = d._picker_data || { chats: [], groups: [] };
        var q = (query || "").toLowerCase();
        var html = "";
        var now = Math.floor(Date.now() / 1000);

        // Helper: format timestamp as relative time
        function formatTime(ts) {
            if (!ts) return "";
            var diff = now - ts;
            if (diff < 60) return "now";
            if (diff < 3600) return Math.floor(diff / 60) + "m";
            if (diff < 86400) return Math.floor(diff / 3600) + "h";
            var d = new Date(ts * 1000);
            var dd = String(d.getDate()).padStart(2, "0");
            var mm = String(d.getMonth() + 1).padStart(2, "0");
            return dd + "/" + mm;
        }

        // Helper: truncate last message to 50 chars
        function truncateMsg(msg) {
            if (!msg) return "";
            return msg.length > 50 ? msg.substring(0, 50) + "…" : msg;
        }

        // Filter
        function matchItem(item) {
            if (!q) return true;
            return (
                (item.name || "").toLowerCase().indexOf(q) >= 0 ||
                (item.id || "").indexOf(q) >= 0 ||
                (item.lastMessage || "").toLowerCase().indexOf(q) >= 0
            );
        }
        var chats = (data.chats || []).filter(matchItem);
        var groups = (data.groups || []).filter(matchItem);

        if (chats.length === 0 && groups.length === 0) {
            html = '<div style="max-height:340px;overflow-y:auto;"><div style="text-align:center;padding:40px 20px;color:#999;font-size:13px;">No chats found.</div></div>';
        } else {
            html = '<div style="max-height:340px;overflow-y:auto;">';

            // Render a single chat item (used for both individual chats and groups)
            function renderItem(item, isGroup) {
                var initials = getInitials(item.name);
                var avatarBg = isGroup
                    ? 'linear-gradient(135deg,#128C7E,#075E54)'
                    : 'linear-gradient(135deg,#25D366,#128C7E)';
                var avatarHtml = '<div style="width:36px;height:36px;border-radius:50%;background:' + avatarBg + ';color:white;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;flex-shrink:0;">' + initials + '</div>';

                var lastMsg = truncateMsg(item.lastMessage);
                var timeStr = formatTime(item.timestamp);
                var unread = item.unreadCount || 0;
                var unreadBadge = unread > 0
                    ? '<div style="background:#25D366;color:white;font-size:10px;font-weight:700;min-width:18px;height:18px;border-radius:9px;display:flex;align-items:center;justify-content:center;padding:0 5px;flex-shrink:0;">' + unread + '</div>'
                    : '';

                html +=
                    '<div class="wa-picker-item" data-chat-id="' +
                    frappe.utils.escape_html(item.id) +
                    '" style="display:flex;align-items:center;gap:10px;padding:8px 10px;cursor:pointer;border-radius:8px;transition:background 0.12s;">' +
                    avatarHtml +
                    '<div style="flex:1;min-width:0;">' +
                    '<div style="display:flex;justify-content:space-between;align-items:center;">' +
                    '<div style="font-weight:500;font-size:13px;color:#1a1a1a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' +
                    frappe.utils.escape_html(item.name) +
                    "</div>" +
                    (timeStr ? '<div style="font-size:10px;color:#999;flex-shrink:0;margin-left:4px;">' + timeStr + "</div>" : "") +
                    "</div>" +
                    (lastMsg ? '<div style="font-size:11px;color:#999;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:1px;">' + frappe.utils.escape_html(lastMsg) + "</div>" : "") +
                    "</div>" +
                    unreadBadge +
                    "</div>";
            }

            if (chats.length > 0) {
                html += '<div style="padding:8px 0 2px;color:#999;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Chats</div>';
                chats.forEach(function (c) { renderItem(c, false); });
            }

            if (groups.length > 0) {
                html += '<div style="padding:8px 0 2px;color:#999;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Groups</div>';
                groups.forEach(function (g) { renderItem(g, true); });
            }
            html += "</div>";
        }

        d.fields_dict.picker_html.$wrapper.html(html);

        // Hover + click
        d.fields_dict.picker_html.$wrapper
            .find(".wa-picker-item")
            .on("mouseenter", function () {
                $(this).css("background", "#f0faf4");
            })
            .on("mouseleave", function () {
                $(this).css("background", "");
            })
            .on("click", function () {
                var chatId = $(this).data("chat-id");
                var chatName = $(this).find("div[style*='font-weight:500']").first().text();
                d.hide();
                sendWithSelectedChat(doctype, name, print_format, _lang, chatId, chatName);
            });
    }

    /* -----------------------------------------------------------
       Helpers
    ----------------------------------------------------------- */
    function filterPickerList(d) {
        var ctx = d._wa_context || {};
        renderPickerList(d, d.fields_dict.search_input.get_value() || "", ctx.doctype, ctx.name, ctx.print_format, ctx._lang);
    }

    function searchContactsServerSide(d) {
        var query = (d.fields_dict.search_input.get_value() || "").trim();
        if (!query) {
            frappe.msgprint("Enter a name or number to search");
            return;
        }

        // Show loading
        var $picker = d.fields_dict.picker_html.$wrapper;
        $picker.html('<div style="text-align:center;padding:30px;color:#999;font-size:13px;">Searching contacts...</div>');

        frappe.call({
            method: "gravures_custom.overrides.search_whatsapp_contacts",
            args: { query: query },
            callback: function (r) {
                if (!r.message) {
                    $picker.html('<div style="text-align:center;padding:30px;color:#e74c3c;">Search failed.</div>');
                    return;
                }
                d._picker_data = r.message;
                var ctx = d._wa_context || {};
                renderPickerList(d, query, ctx.doctype, ctx.name, ctx.print_format, ctx._lang);
            },
            error: function () {
                $picker.html('<div style="text-align:center;padding:30px;color:#e74c3c;">Error searching contacts.</div>');
            },
        });
    }

    function getInitials(name) {
        if (!name) return "?";
        var parts = name.trim().split(/\s+/);
        if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
        return name.substring(0, 2).toUpperCase();
    }

    /* -----------------------------------------------------------
       Send PDF to selected chat
    ----------------------------------------------------------- */
    function sendWithSelectedChat(doctype, name, print_format, _lang, chatId, chatName) {
        // Confirm before sending
        var safeChatName = frappe.utils.escape_html(chatName);
        frappe.confirm(
            "Send PDF to <b>" + safeChatName + "</b> via WhatsApp?",
            function () {
                frappe.show_alert({ message: "Sending PDF to " + safeChatName + "...", indicator: "blue" });

                frappe.call({
                    method: "gravures_custom.overrides.send_print_pdf_whatsapp",
                    args: {
                        doctype: doctype,
                        name: name,
                        print_format: print_format || undefined,
                        chat_id: chatId,
                    },
                    callback: function (r) {
                        if (r.message && r.message.success) {
                            frappe.show_alert({ message: "Sent PDF to " + safeChatName + "!", indicator: "green" });
                        } else if (r._server_messages) {
                            frappe.msgprint(r._server_messages.join("<br>"));
                        } else {
                            frappe.msgprint("Unexpected response from server.");
                        }
                    },
                    error: function (err) {
                        frappe.msgprint("Failed: " + (err._message || "Unknown error."));
                    },
                });
            },
            function () {
                // User cancelled - do nothing
            }
        );
    }

    /* -----------------------------------------------------------
       Button injection into Print Preview toolbar
    ----------------------------------------------------------- */
    function getDocumentInfo() {
        // Try frappe route first (works for doctype routes)
        var route = (window.frappe && frappe.get_route) ? frappe.get_route() : [];
        if (route && route[0] === "print" && route[1] && route[2]) {
            return { doctype: route[1], name: route.slice(2).join("/") };
        }

        // Fallback: parse from URL hash (print preview uses hash routing like #print/print/Doctype/Name)
        try {
            var hash = window.location.hash;
            if (hash.startsWith('#')) hash = hash.substring(1);
            if (hash.startsWith('/')) hash = hash.substring(1);
            var parts = hash.split('/');
            if (parts[0] === "print" && parts[1] && parts[2]) {
                return { doctype: parts[1], name: parts.slice(2).join("/") };
            }
        } catch (e) {}

        return null;
    }

    function getPrintFormat() {
        try {
            var $pf = $("input[data-fieldname='print_format']:visible").first();
            if ($pf.length) return $pf.val() || null;
            var $pfHidden = $("input[data-fieldname='print_format']").first();
            return $pfHidden.length ? $pfHidden.val() || null : null;
        } catch (e) { return null; }
    }

    function getLanguage() {
        try {
            var $l = $("input[data-fieldname='language']:visible").first();
            if ($l.length) return $l.val() || null;
            var $lHidden = $("input[data-fieldname='language']").first();
            return $lHidden.length ? $lHidden.val() || null : null;
        } catch (e) { return null; }
    }

    function hideTryNewMessage() {
        try {
            $(".inner-page-message, a[href*='print-designer']").filter(function () {
                return $(this).text().indexOf("Try the new") >= 0 || ($(this).attr("href") || "").indexOf("print-designer") >= 0;
            }).remove().parent().remove();
        } catch (e) {}
    }


function hasWhatsAppPermission() {
        // Check if user is Engraving User - hide button only for Engraving User
        if (window.frappe && frappe.user && frappe.user_roles) {
            return !frappe.user_roles.includes("Engraving User");
        }
        return true;
    }

    function injectIntoToolbar() {
        try {
            // console.log('[print_whatsapp_v3] injectIntoToolbar called');
            // Guard: only inject on print preview pages
            if (!isPrintRoute()) { return false; }
// Permission check: only System Manager or HR Manager can see the button
            if (!hasWhatsAppPermission()) { return false; }
// Check if button already exists in toolbar
            if ($(".page-actions .custom-actions .btn-whatsapp-gc, .custom-actions .btn-whatsapp-gc").length) {
                // console.log('[print_whatsapp_v3] button already exists');
                return true;
            }

            var $ca = $(".page-actions .custom-actions, .custom-actions");
            // console.log('[print_whatsapp_v3] custom-actions found:', $ca.length);

            if (!$ca || !$ca.length) { return false; }
// Make custom-actions visible on print preview (it has hide class)
            $ca.removeClass('hide hidden-xs hidden-md');

            var $btn = $(
                '<button class="btn btn-sm btn-whatsapp-gc" title="Send PDF to WhatsApp" style="background-color:#25D366;border-color:#25D366;color:white;padding:4px 8px;">' +
                    WA_ICON +
                "</button>"
            );
            $btn.on("click", function (e) {
                e.preventDefault();
                if (!window.frappe || !frappe.call) {
                    alert("Frappe not yet loaded, please try again.");
                    return;
                }
                var doc = getDocumentInfo();
                if (!doc) {
                    frappe.msgprint(__("Cannot determine document from URL."));
                    return;
                }
                showContactPicker(doc.doctype, doc.name, getPrintFormat(), getLanguage());
            });
            // Insert next to Refresh button (last) or prepend
            var $refresh = $ca.find("button:contains('Refresh')").last();
            if ($refresh.length) { $refresh.after($btn); }
            else { $ca.prepend($btn); };
            return true;
        } catch (e) { console.error('[print_whatsapp_v3] inject error:', e); return false; }
    }

/* -----------------------------------------------------------
   Injection engine — print-page only, persistent interval

   Strategy: keep a single persistent timer that checks every
   3000ms whether we're on a print page and whether our button
   is present. If not, inject.  The interval never stops, so
   it catches first navigation AND refresh equally.

   The route guard (`route[0] === "print"`) prevents injection
   on non-print pages (form, list, etc.).
----------------------------------------------------------- */

    function isPrintRoute() {
        try {
            var route = frappe.get_route ? frappe.get_route() : [];
            if (route[0] === "print") return true;
        } catch (e) {}

        // Fallback: check URL hash for print pages
        try {
            var hash = window.location.hash;
            if (hash.startsWith('#')) hash = hash.substring(1);
            if (hash.startsWith('/')) hash = hash.substring(1);
            var parts = hash.split('/');
            if (parts[0] === "print") return true;
        } catch (e) {}

        return false;
    }

    function startInjector() {
        try {
            var injectTimer = null;

            function tryInject() {
                if (!isPrintRoute()) return;
                injectIntoToolbar();
            }

            // Wait for Frappe to be ready, then start the
            // persistent timer and register route handler.
            function onFrappeReady() {
                if (!window.frappe || !frappe.router || !frappe.get_route) {
                    setTimeout(onFrappeReady, 100);
                    return;
                }

                // Persistent check — runs every 3s, catches all scenarios.
                injectTimer = setInterval(tryInject, 3000);

                // Route change — try a few times with delays.
                frappe.router.on("change", function () {
                    var delays = [500, 1500, 3000];
                    for (var i = 0; i < delays.length; i++) {
                        (function (d) { setTimeout(tryInject, d); })(delays[i]);
                    }
                });

                // Try once immediately for good measure.
                setTimeout(tryInject, 400);

                setTimeout(hideTryNewMessage, 200);
                setTimeout(hideTryNewMessage, 600);
                setTimeout(hideTryNewMessage, 1200);
            }

            onFrappeReady();
        } catch (e) {
            console.error('[print_whatsapp_v3] startInjector error:', e);
        }
    };
    startInjector();
})();
