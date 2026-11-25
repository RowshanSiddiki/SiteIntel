// Project workspace controller. Each workspace page renders a subset of the
// elements below, so every lookup is optional and guarded.
(() => {
  const root = document.querySelector("#project-workspace");
  if (!root) return;
  const $ = (selector) => document.querySelector(selector);
  const token = $('meta[name="csrf-token"]')?.content || "";
  const projectId = root.dataset.projectId;
  const form = $("#report-upload-form");
  const fileInput = $("#report-image");
  const previewWrap = $("#report-preview-wrap");
  const preview = $("#report-preview");
  const pdfPreview = $("#report-pdf-preview");
  const uploadButton = $("#extract-report-button");
  const loading = $("#extract-report-loading");
  const error = $("#report-error");
  const workspaceError = $("#workspace-error");
  const results = $("#report-results");
  const reviewForm = $("#review-form");
  const predictionsBox = $("#workspace-predictions");
  const reportList = $("#report-list");
  const historyBox = $("#history-content");
  const overviewReports = $("#overview-reports");
  const overviewPredictions = $("#overview-predictions");
  let project = null;
  let report = null;
  let previewUrl = null;
  let generation = 0;
  let uploadId = null;

  const api = async (url, options = {}) => {
    const headers = new Headers(options.headers || {});
    headers.set("X-CSRFToken", token);
    const response = await fetch(url, { ...options, headers, credentials: "same-origin" });
    const body = await response.text();
    let data = {};
    try { data = body ? JSON.parse(body) : {}; } catch { /* Non-JSON server errors use the status below. */ }
    if (!response.ok) {
      throw new Error(data?.error?.message || `Could not load project data (HTTP ${response.status}).`);
    }
    return data;
  };
  const json = (value) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
  const message = (element, value) => {
    if (!element) return;
    element.textContent = value;
    element.hidden = !value;
  };
  const text = (parent, tag, value, className = "") => {
    const element = document.createElement(tag);
    element.textContent = value;
    if (className) element.className = className;
    parent.append(element);
    return element;
  };
  const renderLoadError = (element, retry) => {
    if (!element) return;
    element.replaceChildren();
    text(element, "p", "Could not load this section.", "empty-copy");
    const button = text(element, "button", "Retry", "text-button");
    button.type = "button";
    button.addEventListener("click", retry, { once: true });
  };
  const date = (value) => value ? value.slice(0, 10) : "";
  const displayPreview = (url, isPdf) => {
    if (!previewWrap) return;
    previewWrap.hidden = false;
    preview.hidden = isPdf;
    pdfPreview.hidden = !isPdf;
    if (isPdf) { pdfPreview.src = url; preview.removeAttribute("src"); }
    else { preview.src = url; pdfPreview.removeAttribute("src"); }
  };

  const hideReview = () => {
    report = null;
    if (results) results.hidden = true;
    if (previewWrap) previewWrap.hidden = true;
  };

  const renderReview = (data) => {
    // Review applies only until the report is confirmed.
    if (!results || data.reviewed) { hideReview(); return; }
    report = data;
    const container = $("#extracted-information");
    container.replaceChildren();
    $("#original-extracted-text").textContent = data.extracted_text;
    $("#ocr-empty-message").hidden = Object.keys(data.fields).length > 0;
    $("#review-status").textContent = "Needs review";
    $("#confirm-report").hidden = !data.editable;
    $("#review-message").textContent = data.editable ? "" : "Only a project owner or editor can confirm information.";
    for (const [label, value] of Object.entries(data.fields)) {
      const wrapper = document.createElement("label");
      wrapper.textContent = label;
      const input = document.createElement("input");
      input.type = "text";
      input.name = label;
      input.value = value;
      input.maxLength = 2000;
      input.readOnly = !data.editable;
      wrapper.append(input);
      container.append(wrapper);
    }
    displayPreview(`/api/reports/documents/${data.document_id}/file`, data.content_type === "application/pdf");
    results.hidden = false;
  };

  const openReport = async (id) => {
    message(error, "");
    try {
      const data = await api(`/api/reports/documents/${id}`);
      if (!project || !project.documents.some((item) => item.id === id)) return;
      renderReview(data);
      if (!data.reviewed) results?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (failure) { message(error, failure.message); }
  };

  const resultText = (item) => {
    if (item.model_key === "cement_demand") return `${item.result.predicted_consumption_tonnes.toFixed(2)} tonnes`;
    if (item.model_key === "concrete_strength") return `${item.result.compressive_strength_mpa.toFixed(2)} MPa`;
    return `${item.result.delay_risk} · ${(item.result.delay_probability * 100).toFixed(1)}%`;
  };
  const inputList = (item) => {
    const list = document.createElement("ul");
    for (const field of Object.values(item.inputs)) {
      text(list, "li", `${field.label}: ${field.value}${field.unit ? ` ${field.unit}` : ""} (${field.source === "ocr" ? "from report" : "entered by you"})`);
    }
    return list;
  };

  const SVG = "http://www.w3.org/2000/svg";
  const ICONS = {
    check: '<path d="M20 6 9 17l-5-5"/>',
    upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    chart: '<path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>',
    dot: '<circle cx="12" cy="12" r="3"/>',
    doc: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    flag: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>',
  };
  const smallIcon = (name) => {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "14");
    svg.setAttribute("height", "14");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = ICONS[name] || ICONS.dot;
    return svg;
  };
  const shortDate = (value) => value
    ? new Date(value).toLocaleDateString("en", { month: "short", day: "numeric" }) : "";
  const EVENT_ICONS = {
    report_confirmed: "check", report_updated: "check", report_uploaded: "upload",
    predictions_generated: "chart", project_created: "flag", construction_record: "doc", expense: "doc",
  };

  // Overview shows the newest events first; the API returns oldest first.
  const renderHistory = (items) => {
    if (!historyBox) return;
    historyBox.replaceChildren();
    const recent = items.slice().reverse().slice(0, 4);
    if (!recent.length) text(historyBox, "p", "No activity yet.", "empty-copy");
    for (const item of recent) {
      const row = document.createElement("div");
      row.className = "history-item";
      const badge = document.createElement("span");
      badge.className = "ov-icon";
      badge.append(smallIcon(EVENT_ICONS[item.type]));
      row.append(badge);
      const info = document.createElement("span");
      info.className = "ov-grow";
      text(info, "strong", item.description);
      if (item.report) text(info, "small", ` · ${item.report}`, "ov-ellipsis");
      row.append(info);
      text(row, "time", shortDate(item.time), "muted");
      historyBox.append(row);
    }
  };

  const PREDICTION_CARDS = [
    ["delay_risk", "Construction Delay Risk",
     (r) => `${r.delay_risk} · ${Math.round(r.delay_probability * 100)}%`],
    ["concrete_strength", "Concrete Strength", (r) => `${r.compressive_strength_mpa.toFixed(2)} MPa`],
    ["cement_demand", "Cement Demand", (r) => `${r.predicted_consumption_tonnes.toFixed(2)} tonnes`],
  ];
  const renderPredictionSummary = (data) => {
    if (!overviewPredictions) return;
    overviewPredictions.replaceChildren();
    const href = overviewPredictions.dataset.href;
    for (const [key, title, format] of PREDICTION_CARDS) {
      const item = data.current.find((entry) => entry.model_key === key);
      const card = document.createElement("a");
      card.className = "ov-card";
      card.href = href;
      text(card, "span", title, "ov-card-title");
      if (item && item.result) text(card, "strong", format(item.result), "ov-card-value");
      else text(card, "strong", "—", "ov-card-value ov-none");
      if (!item || !item.result) {
        text(card, "small", item && item.status === "unavailable" ? "Unavailable" : "No prediction yet", "muted");
      }
      overviewPredictions.append(card);
    }
  };

  const renderPredictions = (data) => {
    if (!predictionsBox) return;
    predictionsBox.replaceChildren();
    predictionsBox.parentElement.querySelectorAll(".earlier-predictions").forEach((item) => item.remove());
    // The API returns exactly one current prediction per model, so a model can
    // never show a result and a missing-information form together.
    const current = data.current;
    if (!current.length) {
      text(predictionsBox, "p", "Upload and confirm a report to see predictions.", "empty-copy");
      return;
    }
    for (const item of current) {
      const card = document.createElement("article");
      card.className = "prediction-card";
      const head = document.createElement("div");
      head.className = "prediction-head";
      text(head, "h4", item.model_name);
      const complete = item.status === "complete";
      text(head, "span", complete ? "Complete" : item.status === "unavailable" ? "Unavailable" : "Needs information", `prediction-status${complete ? "" : " pending"}`);
      card.append(head);
      text(card, "p", item.document_name, "muted");
      if (item.result) text(card, "strong", resultText(item), "result-value");
      else if (item.status === "unavailable") text(card, "p", "This prediction is unavailable. Try again later.");
      if (Object.keys(item.inputs).length) {
        const details = document.createElement("details");
        text(details, "summary", "Information used");
        details.append(inputList(item));
        card.append(details);
      }
      if (item.missing.length) {
        text(card, "p", "More information is needed:");
        const missingForm = document.createElement("form");
        for (const field of item.missing) {
          const label = document.createElement("label");
          label.textContent = `${field.label}${field.unit ? ` (${field.unit})` : ""} · Example: ${field.example}`;
          let input;
          if (field.options) {
            input = document.createElement("select");
            input.add(new Option("Select", ""));
            for (const option of field.options) input.add(new Option(option, option));
          } else {
            input = document.createElement("input");
            input.type = "number"; input.step = "any";
            if (field.name !== "avg_temp_c") input.min = "0";
          }
          input.name = field.name; input.required = true; input.disabled = !data.editable;
          label.append(input);
          text(label, "small", field.reason, "field-help");
          missingForm.append(label);
        }
        if (data.editable) {
          const button = text(missingForm, "button", "Complete prediction", "button button-primary");
          button.type = "submit";
          missingForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (button.disabled || !missingForm.reportValidity()) return;
            button.disabled = true;
            const values = {};
            for (const field of item.missing) {
              const value = missingForm.elements.namedItem(field.name).value;
              values[field.name] = field.options ? value : Number(value);
            }
            try {
              await api(`/api/predictions/${item.id}/complete`, json(values));
              await refreshPredictions();
            } catch (failure) { message(workspaceError, failure.message); button.disabled = false; }
          });
        }
        card.append(missingForm);
      } else if (data.editable && complete) {
        const button = text(card, "button", "Run again", "text-button");
        button.type = "button";
        button.addEventListener("click", async () => {
          button.disabled = true;
          try { await api(`/api/predictions/${item.id}/rerun`, json({})); await refreshPredictions(); }
          catch (failure) { message(workspaceError, failure.message); button.disabled = false; }
        });
      }
      predictionsBox.append(card);
    }
    const currentIds = new Set(current.map((item) => item.id));
    const earlier = data.history.filter((item) => item.status === "complete" && !currentIds.has(item.id));
    if (earlier.length) {
      const details = document.createElement("details");
      details.className = "earlier-predictions";
      text(details, "summary", `Earlier predictions (${earlier.length})`);
      for (const item of earlier) {
        const entry = document.createElement("details");
        text(entry, "summary", `${item.model_name} · ${item.document_name} · ${date(item.created_at)} · ${resultText(item)}`);
        entry.append(inputList(item));
        details.append(entry);
      }
      predictionsBox.after(details);
    }
  };

  const refreshPredictions = async () => {
    if (!project || !(predictionsBox || overviewPredictions)) return;
    const id = project.id;
    try {
      const data = await api(`/api/projects/${id}/predictions`);
      if (project?.id !== id) return;
      renderPredictions(data);
      renderPredictionSummary(data);
      message(workspaceError, "");
    } catch (failure) {
      if (project?.id !== id) return;
      renderLoadError(predictionsBox || overviewPredictions, () => refreshPredictions());
      if (predictionsBox && overviewPredictions) {
        renderLoadError(overviewPredictions, () => refreshPredictions());
      }
      if (workspaceError) message(workspaceError, failure.message);
    }
  };

  const reportRow = (documentItem, withReview) => {
    const row = document.createElement("div"); row.className = "list-row";
    const info = document.createElement("span");
    text(info, "strong", documentItem.name);
    text(info, "small", `${date(documentItem.created_at)} · ${documentItem.reviewed ? "Confirmed" : "Needs review"}`);
    row.append(info);
    if (withReview && !documentItem.reviewed) {
      const button = text(row, "button", "Review", "text-button");
      button.type = "button"; button.addEventListener("click", () => openReport(documentItem.id));
    }
    return row;
  };

  const renderProject = (data) => {
    project = data;
    if (form) form.hidden = !data.editable;
    if (reportList) {
      reportList.replaceChildren();
      if (!data.documents.length) text(reportList, "p", "No reports uploaded yet.", "empty-copy");
      for (const documentItem of data.documents) reportList.append(reportRow(documentItem, true));
    }
    if (overviewReports) {
      overviewReports.replaceChildren();
      if (!data.documents.length) text(overviewReports, "p", "No reports uploaded yet.", "empty-copy");
      for (const documentItem of data.documents.slice(0, 3)) {
        const row = document.createElement("div");
        row.className = "ov-report";
        const badge = document.createElement("span");
        badge.className = "ov-icon";
        badge.append(smallIcon("doc"));
        row.append(badge);
        const info = document.createElement("span");
        info.className = "ov-grow";
        text(info, "strong", documentItem.name, "ov-ellipsis");
        text(info, "small", shortDate(documentItem.created_at), "muted");
        row.append(info);
        text(row, "span", documentItem.reviewed ? "Confirmed" : "Needs review",
          `ov-badge ${documentItem.reviewed ? "ok" : "warn"}`);
        overviewReports.append(row);
      }
    }
    renderHistory(data.history);
  };

  const loadProject = async (id, keepReview = false) => {
    const serial = ++generation;
    message(workspaceError, "");
    if (!keepReview) hideReview();
    try {
      const data = await api(`/api/projects/${encodeURIComponent(id)}`);
      if (serial !== generation) return;
      renderProject(data);
      await refreshPredictions();
    } catch (failure) {
      if (serial !== generation) return;
      message(workspaceError, failure.message);
      renderLoadError(overviewReports, () => loadProject(id));
      renderLoadError(historyBox, () => loadProject(id));
      renderLoadError(overviewPredictions, () => loadProject(id));
      renderLoadError(predictionsBox, () => loadProject(id));
      renderLoadError(reportList, () => loadProject(id));
    }
  };

  if (form) {
    fileInput.addEventListener("change", () => {
      uploadId = null;
      results.hidden = true;
      message(error, "");
      uploadButton.disabled = true;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = null; previewWrap.hidden = true;
      const file = fileInput.files?.[0];
      if (!file) return;
      const extension = file.name.split(".").pop().toLowerCase();
      if (!["jpg", "jpeg", "png", "pdf"].includes(extension) || file.size > 10 * 1024 * 1024) {
        message(error, "Choose a JPG, PNG, or PDF report no larger than 10 MB.");
        fileInput.value = ""; return;
      }
      previewUrl = URL.createObjectURL(file);
      uploadId = crypto.randomUUID().replaceAll("-", "");
      displayPreview(previewUrl, extension === "pdf");
      uploadButton.disabled = false;
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const file = fileInput.files?.[0];
      if (!file || !project?.editable || uploadButton.disabled) return;
      const id = project.id;
      uploadButton.disabled = true; loading.hidden = false; message(error, "");
      const data = new FormData(); data.append("image", file); data.append("project_id", id);
      try {
        const response = await api("/api/reports/extract", { method: "POST", body: data, headers: { "Idempotency-Key": uploadId } });
        if (project?.id !== id) return;
        await loadProject(id);
        await openReport(response.document_id);
        fileInput.value = "";
      } catch (failure) { message(error, failure.message); }
      finally { loading.hidden = true; uploadButton.disabled = !fileInput.files?.length; }
    });
  }
  if (reviewForm) {
    reviewForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!report || !project?.editable) return;
      const activeReport = report;
      const id = project.id;
      const button = $("#confirm-report");
      if (button.disabled) return;
      button.disabled = true;
      const fields = Object.fromEntries(new FormData(reviewForm));
      try {
        await api(`/api/reports/documents/${activeReport.document_id}/confirm`, json({ fields, review_id: activeReport.review_id }));
        if (project?.id !== id || report?.document_id !== activeReport.document_id) return;
        hideReview();
        // Results live on the ML Predictions page, so continue there.
        if (root.dataset.mlUrl) { location.assign(root.dataset.mlUrl); return; }
        await loadProject(id);
      } catch (failure) { $("#review-message").textContent = failure.message; }
      finally { button.disabled = false; }
    });
  }
  loadProject(projectId).catch((failure) => message(workspaceError, failure.message));
})();
