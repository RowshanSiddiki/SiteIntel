// Document Assistant: upload, library, passage search and grounded Q&A.
(() => {
  const root = document.querySelector("#project-workspace");
  const page = document.querySelector("#document-assistant");
  if (!root || !page) return;
  const $ = (selector) => document.querySelector(selector);
  const token = $('meta[name="csrf-token"]')?.content || "";
  const base = `/api/projects/${root.dataset.projectId}/documents`;
  let editable = false;
  const questionPrefill = new URLSearchParams(window.location.search).get("q");
  if (questionPrefill && $("#doc-question")) {
    $("#doc-question").value = questionPrefill.slice(0, 1000);
    $("#doc-ask-panel")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const show = (node, message) => {
    node.textContent = message || "";
    node.hidden = !message;
  };

  async function api(path, options = {}) {
    const headers = { "X-CSRFToken": token, ...(options.headers || {}) };
    let response;
    try {
      response = await fetch(base + path, { credentials: "same-origin", ...options, headers });
    } catch {
      throw new Error("Could not reach the server. Check your connection and try again.");
    }
    let data = null;
    try { data = await response.json(); } catch { /* non-JSON error body */ }
    if (!response.ok) throw new Error(data?.error?.message || "Something went wrong. Please try again.");
    return data;
  }
  const postJson = (path, body) => api(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });

  function formatDate(iso) {
    if (!iso) return "";
    const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }

  function link(text, href, download) {
    const a = el("a", "button button-secondary button-small", text);
    a.href = href;
    if (download) a.setAttribute("download", "");
    else { a.target = "_blank"; a.rel = "noopener"; }
    return a;
  }

  function renderLibrary(documents) {
    const box = $("#doc-library");
    box.replaceChildren();
    if (!documents.length) {
      const empty = el("div", "empty-state compact");
      empty.append(el("h3", null, "No documents yet"), el("p", null, "Uploaded documents will be listed here."));
      box.append(empty);
      return;
    }
    const wrap = el("div", "table-wrap");
    const table = el("table", "data-table");
    const head = el("tr");
    ["Document", "Category", "Uploaded", "Status", "Actions"].forEach((t) => head.append(el("th", null, t)));
    const thead = el("thead");
    thead.append(head);
    table.append(thead);
    const body = el("tbody");
    documents.forEach((doc) => {
      const row = el("tr");
      const name = el("td", "doc-name", doc.file_name);
      if (doc.page_count) name.append(el("small", "muted", ` · ${doc.page_count} page${doc.page_count === 1 ? "" : "s"}`));
      const status = el("td");
      status.append(el("span", `status-pill doc-status-${doc.status}`, doc.status_label));
      if (doc.error) status.append(el("small", "form-error doc-error", doc.error));
      const actions = el("td", "doc-actions");
      actions.append(link("View", doc.view_url), link("Download", doc.download_url, true));
      if (editable) {
        if (doc.status !== "processing") {
          const retry = el("button", "button button-secondary button-small", "Reprocess");
          retry.type = "button";
          retry.addEventListener("click", () => act(retry, () => api(`/${doc.id}/process?force=1`, { method: "POST" })));
          actions.append(retry);
        }
        const del = el("button", "button button-secondary button-small", "Delete");
        del.type = "button";
        del.addEventListener("click", () => {
          if (!window.confirm(`Delete “${doc.file_name}”? This removes the file and its search index.`)) return;
          act(del, () => api(`/${doc.id}`, { method: "DELETE" }));
        });
        actions.append(del);
      }
      row.append(name, el("td", null, doc.category_label), el("td", null, formatDate(doc.uploaded_at)), status, actions);
      body.append(row);
    });
    table.append(body);
    wrap.append(table);
    box.append(wrap);
  }

  async function act(button, task) {
    button.disabled = true;
    try { await task(); } catch (err) { show($("#doc-upload-error"), err.message); }
    await loadLibrary();
  }

  async function loadLibrary() {
    try {
      const data = await api("");
      editable = data.editable;
      $("#doc-upload-panel").hidden = !editable;
      renderLibrary(data.documents);
    } catch (err) {
      const box = $("#doc-library");
      box.replaceChildren(el("p", "form-error", err.message));
    }
  }

  // Upload with XHR so the browser reports real upload progress.
  $("#doc-upload-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const category = $("#doc-category").value;
    const file = $("#doc-file").files[0];
    const error = $("#doc-upload-error");
    const status = $("#doc-upload-status");
    const progress = $("#doc-upload-progress");
    const button = $("#doc-upload-button");
    show(error, "");
    if (!category) return show(error, "Choose a document category.");
    if (!file) return show(error, "Choose a PDF file to upload.");
    if (!file.name.toLowerCase().endsWith(".pdf")) return show(error, "Only PDF files are supported.");

    const form = new FormData();
    form.append("category", category);
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", base);
    xhr.setRequestHeader("X-CSRFToken", token);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) progress.value = Math.round((e.loaded / e.total) * 100);
    };
    xhr.upload.onload = () => show(status, "Reading and indexing the document…");
    const finish = () => { button.disabled = false; progress.hidden = true; };
    xhr.onerror = () => { finish(); show(status, ""); show(error, "Could not reach the server. Please try again."); };
    xhr.onload = async () => {
      finish();
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        show(status, data?.status === "failed"
          ? "The document was uploaded but could not be processed."
          : "Document uploaded and ready for questions.");
        $("#doc-upload-form").reset();
      } else {
        show(status, "");
        show(error, data?.error?.message || "The upload failed. Please try again.");
      }
      await loadLibrary();
    };
    button.disabled = true;
    progress.value = 0;
    progress.hidden = false;
    show(status, "Uploading…");
    xhr.send(form);
  });

  function citationCard(c) {
    const card = el("li", "doc-citation");
    const title = el("div", "doc-citation-title");
    title.append(el("strong", null, `[${c.number}] ${c.document_name}`), el("span", "badge", `Page ${c.page_number}`));
    if (c.reporting_date) title.append(el("span", "badge", `Report date ${c.reporting_date}`));
    const open = link("Open at page " + c.page_number, c.view_url);
    card.append(title, el("blockquote", null, c.excerpt), open);
    return card;
  }

  function renderCitations(parent, citations) {
    if (!citations.length) return;
    parent.append(el("h4", null, "Sources"));
    const list = el("ol", "doc-citations");
    citations.forEach((c) => list.append(citationCard(c)));
    parent.append(list);
  }

  $("#doc-ask-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = $("#doc-question").value.trim();
    const answer = $("#doc-answer");
    show($("#doc-ask-error"), "");
    answer.replaceChildren();
    if (question.length < 3) return show($("#doc-ask-error"), "Enter a longer question.");
    $("#doc-ask-button").disabled = true;
    $("#doc-ask-loading").hidden = false;
    try {
      const data = await postJson("/ask", { question });
      const card = el("div", `doc-answer-card doc-answer-${data.status}`);
      card.append(el("p", "doc-answer-text", data.answer));
      if (data.notice) card.append(el("p", "muted", data.notice));
      else if (data.mode === "extractive" && data.status === "answered") {
        card.append(el("p", "muted", "No language model is configured, so these are the matching passages rather than a written answer."));
      }
      renderCitations(card, data.citations);
      answer.append(card);
    } catch (err) {
      show($("#doc-ask-error"), err.message);
    } finally {
      $("#doc-ask-button").disabled = false;
      $("#doc-ask-loading").hidden = true;
    }
  });

  $("#doc-search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = $("#doc-search-input").value.trim();
    const box = $("#doc-search-results");
    show($("#doc-search-error"), "");
    box.replaceChildren();
    if (query.length < 3) return show($("#doc-search-error"), "Enter at least 3 characters.");
    try {
      const data = await postJson("/search", { question: query });
      if (data.status === "no_documents") box.append(el("p", "muted", "No processed documents are available yet."));
      else if (!data.passages.length) box.append(el("p", "muted", "No matching passages were found."));
      else renderCitations(box, data.passages);
    } catch (err) {
      show($("#doc-search-error"), err.message);
    }
  });

  $("#doc-example-question").addEventListener("click", () => {
    const input = $("#doc-question");
    input.value = $("#doc-example-question").textContent;
    $("#doc-ask-panel").scrollIntoView({ behavior: "smooth" });
    input.focus();
  });

  loadLibrary();
})();
