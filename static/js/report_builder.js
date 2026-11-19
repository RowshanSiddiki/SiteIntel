// Project Report Builder: upload, verify, consolidate and retrieve daily reports.
(() => {
  const root = document.querySelector("#project-workspace");
  const page = document.querySelector("#builder-upload-form");
  if (!root || !page) return;

  const $ = (selector) => document.querySelector(selector);
  const projectId = root.dataset.projectId;
  const token = $('meta[name="csrf-token"]')?.content || "";
  const dateInput = $("#builder-date");
  const uploadButton = $("#builder-upload-button");
  const progress = $("#builder-progress");
  const intake = $("#builder-intake");
  const history = $("#builder-history");
  const generateButton = $("#builder-generate");
  let canEdit = false;

  const node = (tag, className, value) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = value;
    return element;
  };
  const message = (selector, value) => {
    const target = $(selector);
    target.textContent = value || "";
    target.hidden = !value;
  };
  const escapeDate = (date) => encodeURIComponent(date);
  const api = async (url, options = {}) => {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: { "X-CSRFToken": token, ...(options.headers || {}) },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body?.error?.message || "The request failed.");
    return body;
  };
  const json = (body) => ({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  function renderIntake(data) {
    canEdit = data.editable;
    intake.replaceChildren();
    $("#builder-date-caption").textContent = data.reporting_date;
    generateButton.disabled = !canEdit || data.documents.length === 0
      || data.documents.some((item) => !item.reviewed);
    if (!data.documents.length) {
      intake.append(node("p", "empty-copy", "No documents uploaded for this date."));
      return;
    }

    data.documents.forEach((item) => {
      const card = node("article", "builder-source");
      const header = node("div", "builder-source-head");
      const title = node("div");
      title.append(node("strong", "", item.file_name));
      const link = node("a", "text-link", "View original");
      link.href = item.view_url;
      link.target = "_blank";
      link.rel = "noopener";
      title.append(link);
      const status = node("span", `builder-review-status${item.reviewed ? " is-reviewed" : ""}`,
        item.reviewed ? "Reviewed" : "Needs review");
      header.append(title, status);
      card.append(header);
      const ocr = item.ocr || {};
      if (ocr.provider === "local_tesseract") {
        card.append(node(
          "p",
          "builder-ocr-notice",
          "Handwriting recognition is not configured. Verify handwritten details against the original.",
        ));
      } else if (ocr.handwriting_detected || (ocr.low_confidence_fields || []).length) {
        card.append(node(
          "p",
          "builder-ocr-notice",
          "Handwriting detected. Check highlighted values against the original.",
        ));
      }

      const form = node("form", "builder-review-form");
      form.dataset.documentId = item.id;
      const fields = Object.entries(item.fields);
      const lowConfidence = new Set(ocr.low_confidence_fields || []);
      if (!fields.length) {
        form.append(node("p", "muted", "No labeled fields were extracted. You can still confirm this document."));
      }
      fields.forEach(([label, value]) => {
        const isLowConfidence = lowConfidence.has(label);
        const wrapper = node("label", `builder-field${isLowConfidence ? " is-low-confidence" : ""}`);
        wrapper.append(node("span", "", label));
        if (isLowConfidence) {
          wrapper.append(node("small", "builder-field-warning", "Low confidence — verify this value"));
        }
        const isNotes = label.startsWith("Site notes");
        const input = node(isNotes ? "textarea" : "input");
        if (!isNotes) input.type = "text";
        input.name = label;
        input.value = value;
        input.maxLength = isNotes ? 4000 : 2000;
        input.disabled = !canEdit;
        wrapper.append(input);
        form.append(wrapper);
      });
      const save = node("button", "button button-secondary button-small", item.reviewed ? "Save review" : "Confirm details");
      save.type = "submit";
      save.disabled = !canEdit;
      form.append(save);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        save.disabled = true;
        try {
          const reviewedFields = Object.fromEntries(new FormData(form).entries());
          await api(`/api/reports/documents/${item.id}/confirm`, json({
            fields: reviewedFields,
            review_id: item.review_id,
          }));
          message("#builder-error", "");
          await loadIntake();
        } catch (error) {
          message("#builder-error", error.message);
          save.disabled = false;
        }
      });
      card.append(form);
      intake.append(card);
    });
  }

  async function loadIntake() {
    if (!dateInput.value) return;
    try {
      const data = await api(`/api/reports/projects/${projectId}/builder/intake?date=${escapeDate(dateInput.value)}`);
      renderIntake(data);
    } catch (error) {
      message("#builder-error", error.message);
      intake.replaceChildren(node("p", "empty-copy", "Documents could not be loaded."));
    }
  }

  async function loadHistory() {
    try {
      const data = await api(`/api/reports/projects/${projectId}/builder/reports`);
      history.replaceChildren();
      if (!data.reports.length) {
        history.append(node("p", "empty-copy", "Generated reports will appear here."));
        return;
      }
      const list = node("div", "builder-history-list");
      data.reports.forEach((report) => {
        const row = node("article", "builder-history-row");
        const summary = node("div");
        summary.append(node("strong", "", report.reporting_date));
        summary.append(node("small", "muted", `${report.source_count} source document${report.source_count === 1 ? "" : "s"} · Index ${report.processing_status}`));
        const actions = node("div", "builder-history-actions");
        const view = node("a", "button button-secondary button-small", "View");
        view.href = report.url;
        const download = node("a", "button button-secondary button-small", "PDF");
        download.href = report.download_url;
        actions.append(view, download);
        row.append(summary, actions);
        list.append(row);
      });
      history.append(list);
    } catch (error) {
      history.replaceChildren(node("p", "form-error", error.message));
    }
  }

  dateInput.addEventListener("change", loadIntake);
  page.addEventListener("submit", async (event) => {
    event.preventDefault();
    const files = [...$("#builder-files").files];
    if (!files.length || !dateInput.value) {
      message("#builder-error", "Choose a reporting date and at least one file.");
      return;
    }
    uploadButton.disabled = true;
    progress.hidden = false;
    progress.max = files.length;
    progress.value = 0;
    message("#builder-error", "");
    let uploaded = 0;
    const failures = [];
    for (const file of files) {
      message("#builder-status", `Processing ${file.name}…`);
      const body = new FormData();
      body.append("project_id", projectId);
      body.append("reporting_date", dateInput.value);
      body.append("image", file);
      try {
        await api("/api/reports/extract", {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID().replaceAll("-", "") },
          body,
        });
        uploaded += 1;
      } catch (error) {
        failures.push(`${file.name}: ${error.message}`);
      }
      progress.value += 1;
    }
    $("#builder-files").value = "";
    uploadButton.disabled = !canEdit;
    progress.hidden = true;
    message("#builder-status", uploaded ? `${uploaded} document${uploaded === 1 ? "" : "s"} processed.` : "");
    message("#builder-error", failures.join(" "));
    await loadIntake();
  });

  generateButton.addEventListener("click", async () => {
    generateButton.disabled = true;
    message("#builder-error", "");
    message("#builder-status", "Generating report and indexing it for Document Assistant…");
    try {
      const data = await api(`/api/reports/projects/${projectId}/builder/generate`, json({
        reporting_date: dateInput.value,
      }));
      if (data.index_status !== "completed") {
        message("#builder-status", "Report created, but indexing needs attention. Reprocess it in Document Assistant.");
      } else if (data.prediction_status === "failed") {
        message("#builder-status", "Report created and indexed. ML predictions could not be completed; open ML Predictions to retry.");
      } else {
        message("#builder-status", "Daily report ready and searchable.");
      }
      await loadHistory();
      window.location.assign(data.url);
    } catch (error) {
      message("#builder-status", "");
      message("#builder-error", error.message);
      generateButton.disabled = false;
    }
  });

  const today = new Date();
  dateInput.value = [
    today.getFullYear(),
    String(today.getMonth() + 1).padStart(2, "0"),
    String(today.getDate()).padStart(2, "0"),
  ].join("-");
  loadIntake();
  loadHistory();
})();
