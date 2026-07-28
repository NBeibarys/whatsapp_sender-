/* Silkroad Sender frontend — vanilla JS, no frameworks, single file. */
(function () {
  "use strict";

  // ---------- Helpers ----------

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function toast(message, kind) {
    var container = $("#toasts");
    if (!container) return;
    var node = document.createElement("div");
    node.className = "toast" + (kind === "error" ? " error" : kind === "warn" ? " warn" : "");
    node.textContent = message;
    container.appendChild(node);
    setTimeout(function () {
      node.classList.add("leaving");
      setTimeout(function () { node.remove(); }, 320);
    }, 4600);
  }

  async function fetchJSON(url, opts) {
    var res;
    try {
      res = await fetch(url, opts || {});
    } catch (err) {
      toast("Network error — is the server running?", "error");
      throw err;
    }
    var data = null;
    try { data = await res.json(); } catch (err) { /* non-JSON body */ }
    if (!res.ok) {
      var detail = data && data.detail
        ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))
        : ("Request failed (" + res.status + ")");
      toast(detail, "error");
      throw new Error(detail);
    }
    return data;
  }

  function postJSON(url, body) {
    return fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }

  function makePoller(fn, intervalMs) {
    var timer = null;
    function tick() { if (!document.hidden) fn(); }
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && timer) fn();
    });
    return {
      start: function () {
        if (timer) return;
        fn();
        timer = setInterval(tick, intervalMs);
      },
      stop: function () { clearInterval(timer); timer = null; },
    };
  }

  function showOverlay(text) {
    var overlay = $("#overlay");
    if (!overlay) return;
    $("#overlay-text").textContent = text || "Working…";
    overlay.hidden = false;
  }
  function hideOverlay() {
    var overlay = $("#overlay");
    if (overlay) overlay.hidden = true;
  }

  function autosize(textarea) {
    function fit() {
      // scrollHeight-based so prefilled content (including soft-wrapped
      // lines) is fully visible on page init, not just after typing.
      textarea.style.height = "auto";
      textarea.style.height = Math.max(120, Math.min(textarea.scrollHeight + 2, 640)) + "px";
    }
    textarea.addEventListener("input", fit);
    fit();
  }

  function wireDropzone(zone, input, onFiles) {
    zone.addEventListener("click", function () { input.click(); });
    zone.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
    });
    input.addEventListener("change", function () {
      if (input.files.length) onFiles(Array.from(input.files));
      input.value = "";
    });
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (e) {
        e.preventDefault();
        zone.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      zone.addEventListener(name, function (e) {
        e.preventDefault();
        zone.classList.remove("dragover");
      });
    });
    zone.addEventListener("drop", function (e) {
      var files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
      if (files.length) onFiles(files);
    });
  }

  var STATUS_BADGES = {
    pending: "badge-dim",
    sending: "badge-accent",
    sent: "badge-pass",
    failed: "badge-bad",
    needs_review: "badge-warn",
  };

  function statusBadge(status) {
    return '<span class="badge ' + (STATUS_BADGES[status] || "badge-dim") + '">' + esc(status) + "</span>";
  }

  // ---------- Campaign page ----------

  function initCampaign() {
    var newBtn = $("#new-campaign-btn");
    var newCard = $("#new-campaign-card");
    if (newBtn && newCard) {
      newBtn.addEventListener("click", function () {
        newCard.classList.remove("hidden");
        $("#nc-name").focus();
        newCard.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      $("#new-campaign-form").addEventListener("submit", async function (e) {
        e.preventDefault();
        var name = $("#nc-name").value.trim();
        var templateText = $("#nc-template").value;
        if (!name || !templateText.trim()) {
          toast("Name and template are required.", "error");
          return;
        }
        var created = await postJSON("/api/campaigns", { name: name, template_text: templateText });
        window.location.href = "/campaigns?selected=" + created.id;
      });
    }

    var workspace = $("#workspace");
    if (!workspace) return;

    var programId = Number(workspace.dataset.programId);
    var paused = workspace.dataset.paused === "1";

    // --- Pause / resume ---
    var pauseBtn = $("#pause-btn");
    function renderPauseBtn() {
      pauseBtn.textContent = paused ? "Resume campaign" : "Pause campaign";
    }
    renderPauseBtn();
    pauseBtn.addEventListener("click", async function () {
      var action = paused ? "resume" : "pause";
      var result = await postJSON("/api/campaigns/" + programId + "/" + action);
      paused = result.paused;
      renderPauseBtn();
      toast(paused ? "Campaign paused." : "Campaign resumed.");
      refreshStatus();
    });

    // --- Template ---
    var templateText = $("#template-text");
    autosize(templateText);
    $("#save-template-btn").addEventListener("click", async function () {
      await fetchJSON("/api/campaigns/" + programId + "/template", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template_text: templateText.value }),
      });
      toast("Template saved.");
      refreshPreview();
    });

    // --- Preview (WhatsApp chat mock) ---

    var WA_TICKS_SVG = '<svg class="wa-ticks" viewBox="0 0 18 12" width="16" height="11"'
      + ' fill="none" stroke="#8696a0" stroke-width="1.6" stroke-linecap="round"'
      + ' stroke-linejoin="round" aria-hidden="true">'
      + '<path d="M1.5 6.5 L4.5 9.5 L10.5 2.5"></path>'
      + '<path d="M7.5 6.9 L10.1 9.5 L16.5 2.5"></path></svg>';

    function waMetaRow() {
      var now = new Date();
      var time = String(now.getHours()).padStart(2, "0") + ":"
        + String(now.getMinutes()).padStart(2, "0");
      return '<span class="wa-meta">' + time + WA_TICKS_SVG + "</span>";
    }

    // Escape first, then tint any unresolved {{field}} tokens amber.
    function waText(text) {
      return esc(text).replace(/\{\{(\w+)\}\}/g, function (token) {
        return '<span class="wa-token">' + token + "</span>";
      });
    }

    function waAttachmentBody(a) {
      if (a.media_type === "image") {
        return '<img class="wa-img" src="/api/attachments/' + a.id
          + '/file" alt="' + esc(a.file_name) + '">';
      }
      var ext = (a.file_name.split(".").pop() || "").toUpperCase();
      if (ext === a.file_name.toUpperCase() || ext.length > 4) ext = "DOC";
      return '<span class="wa-doc">'
        + '<span class="wa-doc-icon">' + esc(ext) + "</span>"
        + '<span class="wa-doc-text">'
        + '<span class="wa-doc-name">' + esc(a.file_name) + "</span>"
        + '<span class="wa-doc-sub">' + esc(ext) + " document</span>"
        + "</span></span>";
    }

    function waBubble(inner, first) {
      return '<div class="wa-bubble' + (first ? " wa-tail" : "") + '">'
        + inner + waMetaRow() + "</div>";
    }

    async function refreshPreview() {
      var preview = await fetchJSON("/api/campaigns/" + programId + "/preview");
      $("#preview-caption").textContent = preview.using_sample_values
        ? "Using sample values because no contacts are queued yet."
        : "Using the first queued contact: " + preview.preview_contact_name + ".";
      var missing = $("#preview-missing");
      if (preview.missing_fields.length) {
        missing.textContent = "Missing value(s) for this preview and for the actual send: "
          + preview.missing_fields.map(function (f) { return "{{" + f + "}}"; }).join(", ");
        missing.classList.remove("hidden");
      } else {
        missing.classList.add("hidden");
      }

      var name = preview.preview_contact_name || "Recipient";
      $("#wa-header-name").textContent = name;
      var initials = name.trim().split(/\s+/).slice(0, 2).map(function (w) {
        return w.charAt(0).toUpperCase();
      }).join("");
      $("#wa-avatar").textContent = preview.preview_contact_name ? initials : "?";

      // Mirror real send semantics: first attachment carries the message
      // text as its caption; further attachments are bare bubbles.
      var bubbles = [];
      var textHtml = preview.message
        ? '<span class="wa-text">' + waText(preview.message) + "</span>"
        : "";
      if (preview.attachments.length) {
        bubbles.push(waBubble(waAttachmentBody(preview.attachments[0]) + textHtml, true));
        preview.attachments.slice(1).forEach(function (a) {
          bubbles.push(waBubble(waAttachmentBody(a), false));
        });
      } else {
        bubbles.push(waBubble(textHtml, true));
      }
      $("#wa-chat").innerHTML = bubbles.join("");
    }

    // --- Attachments ---
    var pendingFiles = [];
    var attachPendingBox = $("#attach-pending");
    var attachSaveBtn = $("#attach-save-btn");

    async function loadAttachments() {
      var attachments = await fetchJSON("/api/campaigns/" + programId + "/attachments");
      var list = $("#attachment-list");
      if (!attachments.length) {
        list.innerHTML = '<p class="count-caption" style="margin:0;">No attachments yet.</p>';
        return;
      }
      list.innerHTML = attachments.map(function (a) {
        return '<div class="attach-row">'
          + '<span class="attach-name">' + esc(a.file_name) + "</span>"
          + '<span class="attach-type">' + esc(a.media_type) + "</span>"
          + '<button type="button" class="btn btn-sm" data-remove-attachment="' + a.id + '">Remove</button>'
          + "</div>";
      }).join("");
      $$("[data-remove-attachment]", list).forEach(function (btn) {
        btn.addEventListener("click", async function () {
          await fetchJSON("/api/attachments/" + btn.dataset.removeAttachment, { method: "DELETE" });
          toast("Attachment removed.");
          loadAttachments();
          refreshPreview();
        });
      });
    }

    function renderPending() {
      if (!pendingFiles.length) {
        attachPendingBox.classList.add("hidden");
        attachSaveBtn.classList.add("hidden");
        return;
      }
      attachPendingBox.classList.remove("hidden");
      attachSaveBtn.classList.remove("hidden");
      attachSaveBtn.textContent = "Save " + pendingFiles.length + " attachment(s)";
      attachPendingBox.innerHTML = pendingFiles.map(function (f, i) {
        return '<div class="attach-row">'
          + '<span class="attach-name">' + esc(f.name) + "</span>"
          + '<span class="attach-type">pending</span>'
          + '<button type="button" class="btn btn-sm" data-remove-pending="' + i + '">Remove</button>'
          + "</div>";
      }).join("");
      $$("[data-remove-pending]", attachPendingBox).forEach(function (btn) {
        btn.addEventListener("click", function () {
          pendingFiles.splice(Number(btn.dataset.removePending), 1);
          renderPending();
        });
      });
    }

    wireDropzone($("#attach-dropzone"), $("#attach-input"), function (files) {
      pendingFiles = pendingFiles.concat(files);
      renderPending();
    });

    attachSaveBtn.addEventListener("click", async function () {
      if (!pendingFiles.length) return;
      var form = new FormData();
      pendingFiles.forEach(function (f) { form.append("files", f); });
      var result = await fetchJSON("/api/campaigns/" + programId + "/attachments", {
        method: "POST",
        body: form,
      });
      if (result.saved.length) toast("Added " + result.saved.length + " attachment(s).");
      result.skipped.forEach(function (s) {
        toast("Skipped " + s.file_name + ": " + s.reason, "warn");
      });
      pendingFiles = [];
      renderPending();
      loadAttachments();
      refreshPreview();
    });

    // --- Add contacts: tabs ---
    var contactTabs = [
      { btn: $("#tab-csv-btn"), pane: $("#tab-csv") },
      { btn: $("#tab-paste-btn"), pane: $("#tab-paste") },
      { btn: $("#tab-manual-btn"), pane: $("#tab-manual") },
    ];
    contactTabs.forEach(function (tab) {
      tab.btn.addEventListener("click", function () {
        contactTabs.forEach(function (other) {
          var active = other === tab;
          other.btn.classList.toggle("active", active);
          other.btn.setAttribute("aria-selected", String(active));
          other.pane.classList.toggle("hidden", !active);
        });
      });
    });

    // --- CSV flow ---
    var SKIP = "-- skip --";
    var csvState = { file: null, columns: [], validCount: 0 };
    var previewTimer = null;

    function setStep(step) {
      $$("#csv-rail .rail-step").forEach(function (node) {
        var n = Number(node.dataset.step);
        node.classList.toggle("done", n < step);
        node.classList.toggle("active", n === step);
      });
      $("#csv-step-map").classList.toggle("hidden", step < 2);
      $("#csv-step-preview").classList.toggle("hidden", step < 3);
    }

    function fillSelect(select, options, chosen) {
      select.innerHTML = options.map(function (opt) {
        return '<option value="' + esc(opt) + '"' + (opt === chosen ? " selected" : "") + ">"
          + esc(opt) + "</option>";
      }).join("");
    }

    function mappingValues() {
      return {
        phone: $("#csv-map-phone").value,
        name: $("#csv-map-name").value,
        startup: $("#csv-map-startup").value,
        email: $("#csv-map-email").value,
      };
    }

    function renderExtraChips() {
      var mapping = mappingValues();
      var used = new Set([mapping.phone, mapping.name]);
      if (mapping.startup !== SKIP) used.add(mapping.startup);
      if (mapping.email !== SKIP) used.add(mapping.email);
      var leftovers = csvState.columns.filter(function (c) { return !used.has(c); });
      var previouslyOff = new Set(
        $$("#csv-extra-columns input").filter(function (cb) { return !cb.checked; })
          .map(function (cb) { return cb.value; })
      );
      var box = $("#csv-extra-columns");
      if (!leftovers.length) {
        box.innerHTML = '<span class="count-caption">No other columns.</span>';
        return;
      }
      box.innerHTML = leftovers.map(function (col) {
        var on = !previouslyOff.has(col);
        return '<label class="chip' + (on ? " on" : "") + '">'
          + '<input type="checkbox" value="' + esc(col) + '"' + (on ? " checked" : "") + ">"
          + esc(col) + "</label>";
      }).join("");
      $$("#csv-extra-columns input", box).forEach(function (cb) {
        cb.addEventListener("change", function () {
          cb.closest(".chip").classList.toggle("on", cb.checked);
          schedulePreview();
        });
      });
    }

    function csvFormData() {
      var mapping = mappingValues();
      var form = new FormData();
      form.append("file", csvState.file);
      form.append("phone_column", mapping.phone);
      form.append("name_column", mapping.name);
      if (mapping.startup !== SKIP) form.append("startup_name_column", mapping.startup);
      if (mapping.email !== SKIP) form.append("email_column", mapping.email);
      var extras = $$("#csv-extra-columns input")
        .filter(function (cb) { return cb.checked; })
        .map(function (cb) { return cb.value; });
      form.append("extra_columns", JSON.stringify(extras));
      return form;
    }

    function renderRows(rows, columns) {
      if (!rows.length) return "";
      return '<div class="tbl-wrap"><table class="tbl"><thead><tr>'
        + columns.map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("")
        + "</tr></thead><tbody>"
        + rows.map(function (row) {
          return "<tr>" + columns.map(function (c) {
            var value = row[c];
            return value == null || value === ""
              ? '<td class="empty">&mdash;</td>'
              : "<td>" + esc(value) + "</td>";
          }).join("") + "</tr>";
        }).join("")
        + "</tbody></table></div>";
    }

    function schedulePreview() {
      clearTimeout(previewTimer);
      previewTimer = setTimeout(function () {
        runCsvPreview().catch(function (err) {
          toast(err && err.message ? err.message : "CSV preview failed.", "error");
        });
      }, 250);
    }

    async function runCsvPreview() {
      if (!csvState.file) return;
      var result = await fetchJSON("/api/campaigns/" + programId + "/contacts/csv", {
        method: "POST",
        body: csvFormData(),
      });
      csvState.validCount = result.valid_count;
      $("#csv-counts").innerHTML =
        '<span class="mono" style="color: var(--accent);">' + result.valid_count + "</span> valid row(s), "
        + '<span class="mono" style="color:' + (result.invalid_count ? "var(--bad)" : "var(--fg-faint)") + ';">'
        + result.invalid_count + "</span> invalid row(s)";
      var validBox = $("#csv-valid-preview");
      if (result.valid_preview.length) {
        var cols = [];
        result.valid_preview.forEach(function (row) {
          Object.keys(row).forEach(function (k) { if (cols.indexOf(k) < 0) cols.push(k); });
        });
        validBox.innerHTML = '<div class="label">Preview (first 10)</div>' + renderRows(result.valid_preview, cols);
      } else {
        validBox.innerHTML = "";
      }
      var invalidBox = $("#csv-invalid-preview");
      if (result.invalid_rows.length) {
        var mapping = mappingValues();
        var rejectedCols = ["row", "error", "phone", "name"];
        if (mapping.startup !== SKIP) rejectedCols.push("startup_name");
        if (mapping.email !== SKIP) rejectedCols.push("email");
        var rejected = result.invalid_rows.map(function (item) {
          var entry = {
            row: item.row_number,
            error: item.error,
            phone: (item.row || {})[mapping.phone] || "",
            name: (item.row || {})[mapping.name] || "",
          };
          if (mapping.startup !== SKIP) entry.startup_name = item.startup_name || "";
          if (mapping.email !== SKIP) entry.email = item.email || "";
          return entry;
        });
        invalidBox.innerHTML = '<div class="label">Rejected</div>' + renderRows(rejected, rejectedCols);
      } else {
        invalidBox.innerHTML = "";
      }
      var commitBtn = $("#csv-commit-btn");
      commitBtn.textContent = "Queue " + result.valid_count + " contact(s)";
      commitBtn.disabled = result.valid_count === 0;
      setStep(3);
    }

    function resetCsvFlow() {
      csvState = { file: null, columns: [], validCount: 0 };
      $("#csv-file-name").classList.add("hidden");
      $("#csv-valid-preview").innerHTML = "";
      $("#csv-invalid-preview").innerHTML = "";
      setStep(1);
    }

    wireDropzone($("#csv-dropzone"), $("#csv-input"), async function (files) {
      var file = files[0];
      if (!/\.csv$/i.test(file.name)) {
        toast("Please upload a .csv file for contacts.", "error");
        return;
      }
      var form = new FormData();
      form.append("file", file);
      var result = await fetchJSON("/api/campaigns/" + programId + "/contacts/csv/columns", {
        method: "POST",
        body: form,
      });
      csvState.file = file;
      csvState.columns = result.columns;
      var fileName = $("#csv-file-name");
      fileName.textContent = file.name;
      fileName.classList.remove("hidden");
      var optional = [SKIP].concat(result.columns);
      fillSelect($("#csv-map-phone"), result.columns, result.guessed.phone || result.columns[0]);
      fillSelect($("#csv-map-name"), result.columns, result.guessed.name || result.columns[0]);
      fillSelect($("#csv-map-startup"), optional, result.guessed.startup || SKIP);
      fillSelect($("#csv-map-email"), optional, result.guessed.email || SKIP);
      $("#csv-extra-columns").innerHTML = "";
      renderExtraChips();
      setStep(2);
      runCsvPreview();
    });

    ["#csv-map-phone", "#csv-map-name", "#csv-map-startup", "#csv-map-email"].forEach(function (sel) {
      $(sel).addEventListener("change", function () {
        renderExtraChips();
        schedulePreview();
      });
    });

    $("#csv-commit-btn").addEventListener("click", async function () {
      var result = await fetchJSON("/api/campaigns/" + programId + "/contacts/csv/commit", {
        method: "POST",
        body: csvFormData(),
      });
      toast("Queued " + result.inserted + " contact(s).");
      if (result.duplicates.length) {
        toast("Skipped " + result.duplicates.length + " duplicate(s): " + result.duplicates.join(", "), "warn");
      }
      resetCsvFlow();
      refreshPreview();
      refreshStatus();
    });

    $("#csv-reset-btn").addEventListener("click", resetCsvFlow);

    // --- Paste list flow ---
    var pasteTextarea = $("#paste-text");
    var pasteSpinner = $("#paste-spinner");
    var pasteCommitBtn = $("#paste-commit-btn");
    var pasteTimer = null;
    var pasteSeq = 0;
    var pasteValidCount = 0;

    function clearPastePreview() {
      pasteValidCount = 0;
      $("#paste-preview").classList.add("hidden");
      $("#paste-counts").innerHTML = "";
      $("#paste-valid-preview").innerHTML = "";
      $("#paste-invalid-preview").innerHTML = "";
      pasteCommitBtn.disabled = true;
      pasteCommitBtn.textContent = "Queue contacts";
    }

    async function runPastePreview() {
      var text = pasteTextarea.value;
      var seq = ++pasteSeq;
      if (!text.trim()) {
        pasteSpinner.classList.add("hidden");
        clearPastePreview();
        return;
      }
      pasteSpinner.classList.remove("hidden");
      var result;
      try {
        result = await postJSON("/api/campaigns/" + programId + "/contacts/paste", { text: text });
      } finally {
        if (seq === pasteSeq) pasteSpinner.classList.add("hidden");
      }
      if (seq !== pasteSeq) return; // user kept typing — a newer parse is coming

      pasteValidCount = result.valid_count;
      $("#paste-preview").classList.remove("hidden");
      $("#paste-counts").innerHTML =
        '<span class="mono" style="color: var(--accent);">' + result.valid_count + "</span> valid, "
        + '<span class="mono" style="color:' + (result.invalid_count ? "var(--bad)" : "var(--fg-faint)") + ';">'
        + result.invalid_count + "</span> invalid";

      var validBox = $("#paste-valid-preview");
      if (result.valid_preview.length) {
        var cols = [];
        result.valid_preview.forEach(function (row) {
          Object.keys(row).forEach(function (k) { if (cols.indexOf(k) < 0) cols.push(k); });
        });
        validBox.innerHTML = '<div class="label">Preview (first 10)</div>'
          + renderRows(result.valid_preview, cols);
      } else {
        validBox.innerHTML = "";
      }

      var invalidBox = $("#paste-invalid-preview");
      if (result.invalid_rows.length) {
        var rejected = result.invalid_rows.map(function (item) {
          var line = item.line || "";
          return {
            row: item.row_number,
            line: line.length > 60 ? line.slice(0, 57) + "…" : line,
            error: item.error,
          };
        });
        invalidBox.innerHTML = '<div class="block block-bad"><div class="label">Rejected lines — edit them above and they re-parse automatically</div>'
          + renderRows(rejected, ["row", "line", "error"]) + "</div>";
      } else {
        invalidBox.innerHTML = "";
      }

      pasteCommitBtn.textContent = "Queue " + result.valid_count + " contact(s)";
      pasteCommitBtn.disabled = result.valid_count === 0;
    }

    pasteTextarea.addEventListener("input", function () {
      // Text changed: any shown preview is stale. Lock committing and mark
      // in-flight responses stale (seq bump) until the parse for the CURRENT
      // text lands — only that response re-enables the Queue button.
      pasteSeq++;
      pasteValidCount = 0;
      pasteCommitBtn.disabled = true;
      pasteSpinner.classList.remove("hidden");
      clearTimeout(pasteTimer);
      pasteTimer = setTimeout(function () {
        runPastePreview().catch(function () { /* toast already shown */ });
      }, 500);
    });

    pasteCommitBtn.addEventListener("click", async function () {
      if (!pasteValidCount) return;
      var result = await postJSON(
        "/api/campaigns/" + programId + "/contacts/paste/commit",
        { text: pasteTextarea.value }
      );
      toast("Queued " + result.inserted + " contact(s).");
      if (result.duplicates.length) {
        toast("Skipped " + result.duplicates.length + " duplicate(s): " + result.duplicates.join(", "), "warn");
      }
      pasteTextarea.value = "";
      clearPastePreview();
      refreshPreview();
      refreshStatus();
    });

    // --- Manual add ---
    $("#manual-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      var result = await postJSON("/api/campaigns/" + programId + "/contacts", {
        phone: $("#manual-phone").value,
        name: $("#manual-name").value,
      });
      if (result.inserted) {
        toast("Added " + result.contact.name + " (" + result.contact.phone + ").");
        $("#manual-phone").value = "";
        $("#manual-name").value = "";
        refreshPreview();
        refreshStatus();
      }
      if (result.duplicates.length) {
        toast(result.duplicates[0] + " is already in this campaign.", "warn");
      }
    });

    // --- Status + contacts (2s poll) ---
    var FILTER_STATUSES = ["pending", "sending", "sent", "failed", "needs_review"];
    var filterOn = new Set();
    var selection = new Set();
    var lastStatusSig = "";
    var lastTableSig = "";
    var latestContacts = [];
    var latestStatus = null;

    // Filter chips
    $("#status-filter").innerHTML = FILTER_STATUSES.map(function (status) {
      return '<label class="chip"><input type="checkbox" value="' + status + '">' + status + "</label>";
    }).join("");
    $$("#status-filter input").forEach(function (cb) {
      cb.addEventListener("change", function () {
        cb.closest(".chip").classList.toggle("on", cb.checked);
        if (cb.checked) filterOn.add(cb.value); else filterOn.delete(cb.value);
        lastTableSig = "";
        renderContacts();
      });
    });

    function shownContacts() {
      return latestContacts.filter(function (c) {
        return filterOn.size === 0 || filterOn.has(c.status);
      });
    }

    function renderStatusTiles(status) {
      var counts = status.counts;
      var pendingNote = status.eta_minutes != null
        ? "~" + status.eta_minutes + " min remaining"
        : (counts.pending && status.paused ? "Paused" : "No active queue");
      var tiles = [
        { label: "Pending", value: counts.pending, note: pendingNote, cls: "" },
        { label: "Sent", value: counts.sent, note: "Delivered by sender", cls: "tile-accent" },
        { label: "Replied", value: status.replied_count, note: "Contacts with replies", cls: "" },
        {
          label: "Failed", value: counts.failed,
          note: counts.failed ? "Needs retry" : "No failures",
          cls: counts.failed ? "tile-bad" : "",
        },
      ];
      if (counts.needs_review) {
        tiles.push({ label: "Needs review", value: counts.needs_review, note: "Manual decision", cls: "tile-warn" });
      }
      $("#status-tiles").innerHTML = tiles.map(function (t) {
        return '<div class="tile ' + t.cls + '">'
          + '<div class="tile-label">' + t.label + "</div>"
          + '<div class="tile-value">' + t.value + "</div>"
          + '<div class="tile-note">' + esc(t.note) + "</div>"
          + "</div>";
      }).join("");

      var actions = [];
      if (counts.failed) {
        actions.push('<button type="button" class="btn" id="retry-all-btn">Retry all ' + counts.failed + " failed</button>");
      }
      if (counts.needs_review) {
        actions.push('<button type="button" class="btn" id="resolve-sent-btn">Mark needs_review as sent</button>');
        actions.push('<button type="button" class="btn" id="resolve-pending-btn">Mark needs_review as pending</button>');
      }
      if (status.paused) {
        actions.push('<button type="button" class="btn btn-primary" id="send-btn"'
          + (counts.pending ? "" : " disabled") + ">Send</button>");
      }
      $("#status-actions").innerHTML = actions.join("");

      var retryAll = $("#retry-all-btn");
      if (retryAll) retryAll.addEventListener("click", async function () {
        var result = await postJSON("/api/campaigns/" + programId + "/contacts/retry-failed");
        toast("Retrying " + result.retried + " contact(s).");
        refreshStatus();
      });
      var resolveSent = $("#resolve-sent-btn");
      if (resolveSent) resolveSent.addEventListener("click", async function () {
        var result = await postJSON("/api/campaigns/" + programId + "/needs-review/resolve", { to: "sent" });
        toast("Marked " + result.updated + " contact(s) as sent.");
        refreshStatus();
      });
      var resolvePending = $("#resolve-pending-btn");
      if (resolvePending) resolvePending.addEventListener("click", async function () {
        var result = await postJSON("/api/campaigns/" + programId + "/needs-review/resolve", { to: "pending" });
        toast("Marked " + result.updated + " contact(s) as pending.");
        refreshStatus();
      });
      var sendBtn = $("#send-btn");
      if (sendBtn) sendBtn.addEventListener("click", async function () {
        await postJSON("/api/campaigns/" + programId + "/resume");
        paused = false;
        renderPauseBtn();
        toast("Campaign is active. The worker will send pending contacts.");
        refreshStatus();
      });
    }

    function renderSelectionCaption() {
      $("#selection-caption").textContent =
        selection.size + " selected / " + shownContacts().length + " shown";
      $("#retry-selected-btn").disabled = selection.size === 0;
      $("#delete-selected-btn").disabled = selection.size === 0;
    }

    function renderContacts() {
      var shown = shownContacts();
      // Prune selection to visible ids.
      var shownIds = new Set(shown.map(function (c) { return c.id; }));
      Array.from(selection).forEach(function (id) {
        if (!shownIds.has(id)) selection.delete(id);
      });

      var sig = JSON.stringify(shown) + "|" + Array.from(selection).sort().join(",");
      if (sig === lastTableSig) { renderSelectionCaption(); return; }
      lastTableSig = sig;

      var box = $("#contacts-table");
      if (!shown.length) {
        box.innerHTML = '<p class="help" style="margin:0;">'
          + (latestContacts.length ? "No contacts match this filter." : "No contacts yet — use Add contacts above.")
          + "</p>";
        renderSelectionCaption();
        return;
      }
      box.innerHTML = '<div class="tbl-wrap"><table class="tbl"><thead><tr>'
        + "<th></th><th>phone</th><th>name</th><th>status</th><th>sent_at</th><th>replied_at</th><th>error_message</th>"
        + "</tr></thead><tbody>"
        + shown.map(function (c) {
          var selected = selection.has(c.id);
          function cell(value) {
            return value ? "<td>" + esc(value) + "</td>" : '<td class="empty">&mdash;</td>';
          }
          return '<tr data-id="' + c.id + '"' + (selected ? ' class="selected"' : "") + ">"
            + '<td><input type="checkbox" class="cb" data-id="' + c.id + '"'
            + (selected ? " checked" : "") + ' aria-label="Select ' + esc(c.name) + '"></td>'
            + "<td>" + esc(c.phone) + "</td>"
            + "<td>" + esc(c.name) + "</td>"
            + "<td>" + statusBadge(c.status) + "</td>"
            + cell(c.sent_at) + cell(c.replied_at) + cell(c.error_message)
            + "</tr>";
        }).join("")
        + "</tbody></table></div>";

      $$("input.cb", box).forEach(function (cb) {
        cb.addEventListener("change", function () {
          var id = Number(cb.dataset.id);
          if (cb.checked) selection.add(id); else selection.delete(id);
          cb.closest("tr").classList.toggle("selected", cb.checked);
          lastTableSig = "";
          renderSelectionCaption();
        });
      });
      renderSelectionCaption();
    }

    $("#select-all-btn").addEventListener("click", function () {
      shownContacts().forEach(function (c) { selection.add(c.id); });
      lastTableSig = "";
      renderContacts();
    });
    $("#clear-selection-btn").addEventListener("click", function () {
      selection.clear();
      lastTableSig = "";
      renderContacts();
    });

    $("#retry-selected-btn").addEventListener("click", async function () {
      if (!selection.size) return;
      var result = await postJSON("/api/contacts/retry", { ids: Array.from(selection) });
      toast("Retrying " + result.retried + " failed contact(s).");
      refreshStatus();
    });

    $("#delete-selected-btn").addEventListener("click", async function () {
      if (!selection.size) return;
      if (!window.confirm("Delete " + selection.size + " selected contact(s)?")) return;
      var result = await postJSON("/api/contacts/delete", { ids: Array.from(selection) });
      toast("Deleted " + result.deleted + " contact(s).");
      if (result.skipped_ids.length) {
        toast("Skipped " + result.skipped_ids.length + " contact(s) currently being sent.", "warn");
      }
      selection.clear();
      refreshStatus();
    });

    async function refreshStatus() {
      var results = await Promise.all([
        fetchJSON("/api/campaigns/" + programId + "/status"),
        fetchJSON("/api/campaigns/" + programId + "/contacts"),
      ]);
      latestStatus = results[0];
      latestContacts = results[1];
      if (latestStatus.paused !== paused) {
        paused = latestStatus.paused;
        renderPauseBtn();
      }
      var statusSig = JSON.stringify(latestStatus);
      if (statusSig !== lastStatusSig) {
        lastStatusSig = statusSig;
        renderStatusTiles(latestStatus);
      }
      renderContacts();
    }

    // Initial loads + poll
    loadAttachments();
    refreshPreview();
    makePoller(function () {
      refreshStatus().catch(function () { /* toast already shown */ });
    }, 2000).start();
  }

  // ---------- Connection page ----------

  function initConnection() {
    var waitingForQR = false;
    var lastQR = null;

    function renderStatus(status) {
      var box = $("#conn-status");
      var age = status.age_seconds != null ? Math.round(status.age_seconds) : null;
      var html;
      if (age == null && !status.worker_alive) {
        html = '<div class="block block-bad">Worker has never checked in. '
          + "The app starts the worker automatically on launch &mdash; if this persists, check "
          + '<span class="mono">data/worker.log</span>.</div>';
      } else if (!status.worker_alive) {
        html = '<div class="block block-bad">Worker may not be running &mdash; last seen '
          + '<span class="mono">' + age + "s</span> ago. Check <span class=\"mono\">data/worker.log</span>.</div>";
      } else if (status.connected) {
        html = '<div class="block block-pass"><div class="row-between">'
          + '<span><span class="badge badge-pass">connected</span>&nbsp; WhatsApp connected &mdash; worker last seen '
          + '<span class="mono">' + age + "s</span> ago.</span>"
          + '<button type="button" class="btn" id="disconnect-btn">Disconnect WhatsApp</button>'
          + "</div></div>";
      } else {
        html = '<div class="block block-warn">WhatsApp not connected &mdash; worker last seen '
          + '<span class="mono">' + age + "s</span> ago. Scan the QR code below to link a number.</div>";
      }
      box.innerHTML = html;
      var disconnectBtn = $("#disconnect-btn");
      if (disconnectBtn) disconnectBtn.addEventListener("click", async function () {
        await postJSON("/api/connection/disconnect");
        waitingForQR = true;
        showOverlay("Preparing fresh QR code…");
        poll();
      });

      var workerMessage = $("#worker-message");
      if (status.worker_message) {
        workerMessage.textContent = status.worker_message;
        workerMessage.classList.remove("hidden");
      } else {
        workerMessage.classList.add("hidden");
      }
    }

    function renderQR(status) {
      var card = $("#qr-card");
      if (status.qr_data_url) {
        if (status.qr_data_url !== lastQR) {
          $("#qr-img").src = status.qr_data_url;
          lastQR = status.qr_data_url;
        }
        card.classList.remove("hidden");
        waitingForQR = false;
        hideOverlay();
      } else {
        card.classList.add("hidden");
        lastQR = null;
      }
      if (status.connected) {
        waitingForQR = false;
        hideOverlay();
      }
      if ((waitingForQR || status.disconnect_requested) && !status.qr_data_url && !status.connected) {
        showOverlay("Preparing fresh QR code…");
      }
    }

    var poller = makePoller(async function () {
      var status = await fetchJSON("/api/connection/status");
      renderStatus(status);
      renderQR(status);
    }, 2000);
    function poll() {
      fetchJSON("/api/connection/status").then(function (status) {
        renderStatus(status);
        renderQR(status);
      }).catch(function () { /* toast shown */ });
    }
    poller.start();

    $("#qr-refresh-btn").addEventListener("click", poll);

    $("#test-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      var result = await postJSON("/api/connection/test-message", {
        phone: $("#test-phone").value,
        name: $("#test-name").value,
      });
      if (result.queued) {
        toast("Queued a test message to " + result.phone + " in the '" + result.program + "' program.");
        $("#test-phone").value = "";
      }
      if (result.duplicate) {
        toast(result.phone + " is already queued in the '" + result.program
          + "' program — use a different number, or wait for it to be sent.", "warn");
      }
    });
  }

  // ---------- Settings page ----------

  function initSettings() {
    var dryRun = $("#set-dry-run");
    var delay = $("#set-delay");
    var jitter = $("#set-jitter");
    var cap = $("#set-cap");

    function intValue(input) {
      var value = parseInt(input.value, 10);
      return isNaN(value) || value < 0 ? 0 : value;
    }

    function updateLiveBadge() {
      $("#live-badge").classList.toggle("hidden", dryRun.checked);
    }

    function updatePacingCaption() {
      var d = intValue(delay);
      var j = intValue(jitter);
      $("#pacing-caption").textContent = "Each send waits " + d + "–" + (d + j) + "s";
    }

    dryRun.addEventListener("change", updateLiveBadge);
    delay.addEventListener("input", updatePacingCaption);
    jitter.addEventListener("input", updatePacingCaption);

    fetchJSON("/api/settings").then(function (settings) {
      dryRun.checked = settings.dry_run;
      delay.value = settings.delay_seconds;
      jitter.value = settings.jitter_seconds;
      cap.value = settings.daily_cap != null ? settings.daily_cap : 0;
      updateLiveBadge();
      updatePacingCaption();
    });

    $("#save-settings-btn").addEventListener("click", async function () {
      var numericFields = [[delay, "delay"], [jitter, "jitter"], [cap, "daily cap"]];
      for (var i = 0; i < numericFields.length; i++) {
        if (isNaN(parseInt(numericFields[i][0].value, 10))) {
          toast("Enter a number for " + numericFields[i][1] + ".", "error");
          return;
        }
      }
      var saved = await fetchJSON("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dry_run: dryRun.checked,
          delay_seconds: intValue(delay),
          jitter_seconds: intValue(jitter),
          daily_cap: intValue(cap),
        }),
      });
      dryRun.checked = saved.dry_run;
      delay.value = saved.delay_seconds;
      jitter.value = saved.jitter_seconds;
      cap.value = saved.daily_cap != null ? saved.daily_cap : 0;
      updateLiveBadge();
      updatePacingCaption();
      toast("Settings saved.");
      $("#saved-card").classList.remove("hidden");
      $("#saved-summary").innerHTML =
        "<dt>Mode</dt><dd>" + (saved.dry_run ? "dry run" : "LIVE SENDING") + "</dd>"
        + "<dt>Delay</dt><dd>" + saved.delay_seconds + "s</dd>"
        + "<dt>Jitter</dt><dd>" + saved.jitter_seconds + "s</dd>"
        + "<dt>Daily cap</dt><dd>" + (saved.daily_cap != null ? saved.daily_cap : "no limit") + "</dd>";
    });
  }

  // ---------- Boot ----------

  document.addEventListener("DOMContentLoaded", function () {
    var page = document.body.dataset.page;
    if (page === "campaign") initCampaign();
    else if (page === "connection") initConnection();
    else if (page === "settings") initSettings();
  });
})();
