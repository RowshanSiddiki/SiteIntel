(() => {
  const form = document.querySelector("#report-upload-form");
  if (!(form instanceof HTMLFormElement)) return;

  const fileInput = document.querySelector("#report-image");
  const previewWrap = document.querySelector("#report-preview-wrap");
  const preview = document.querySelector("#report-preview");
  const extractButton = document.querySelector("#extract-report-button");
  const extractLoading = document.querySelector("#extract-report-loading");
  const errorMessage = document.querySelector("#report-error");
  const results = document.querySelector("#report-results");
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  const maxBytes = 10 * 1024 * 1024;
  const allowedExtensions = new Set(["jpg", "jpeg", "png"]);
  let previewUrl = null;

  const fieldLabel = (name) =>
    name
      .replaceAll("_", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());

  const setError = (message) => {
    errorMessage.textContent = message;
    errorMessage.hidden = !message;
  };

  const apiRequest = async (url, options = {}) => {
    const headers = new Headers(options.headers || {});
    if (csrfToken) headers.set("X-CSRFToken", csrfToken);
    const response = await fetch(url, {
      ...options,
      headers,
      credentials: "same-origin",
    });
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error("The server returned an unreadable response.");
    }
    if (!response.ok) {
      const error = payload?.error;
      const details = [
        ...(error?.missing_fields || []),
        ...(error?.invalid_fields || []),
        ...(error?.unexpected_fields || []),
      ];
      throw new Error(
        `${error?.message || `The request failed (${response.status}).`}${
          details.length ? ` ${details.join(", ")}` : ""
        }`,
      );
    }
    return payload;
  };

  const renderInformation = (groups) => {
    const container = document.querySelector("#extracted-information");
    container.replaceChildren();
    const groupLabels = { report: "Extracted report fields" };

    for (const [groupName, values] of Object.entries(groups || {})) {
      const entries = Object.entries(values || {}).filter(
        ([, value]) => value !== null && value !== undefined && value !== "",
      );
      if (!entries.length) continue;

      const section = document.createElement("section");
      section.className = "extracted-group";
      const heading = document.createElement("h4");
      heading.textContent = groupLabels[groupName] || fieldLabel(groupName);
      section.append(heading);

      const list = document.createElement("dl");
      list.className = "extracted-field-list";
      for (const [name, value] of entries) {
        const row = document.createElement("div");
        const term = document.createElement("dt");
        const description = document.createElement("dd");
        term.textContent = fieldLabel(name);
        description.textContent = String(value);
        row.append(term, description);
        list.append(row);
      }
      section.append(list);
      container.append(section);
    }
  };

  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    results.hidden = true;
    setError("");
    extractButton.disabled = true;

    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    previewWrap.hidden = true;

    if (!file) return;
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (!extension || !allowedExtensions.has(extension)) {
      setError("Choose a JPG, JPEG, or PNG image.");
      fileInput.value = "";
      return;
    }
    if (file.size > maxBytes) {
      setError("The image must be 10 MB or smaller.");
      fileInput.value = "";
      return;
    }
    if (file.type && !["image/jpeg", "image/png"].includes(file.type)) {
      setError("Choose a JPG, JPEG, or PNG image.");
      fileInput.value = "";
      return;
    }

    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
    previewWrap.hidden = false;
    extractButton.disabled = false;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = fileInput.files?.[0];
    if (!file || extractButton.disabled) return;

    setError("");
    results.hidden = true;
    extractButton.disabled = true;
    extractLoading.hidden = false;
    const data = new FormData();
    data.append("image", file);

    try {
      const response = await apiRequest("/api/reports/extract", {
        method: "POST",
        body: data,
      });
      document.querySelector("#original-extracted-text").textContent =
        response.extracted_text || "";
      document.querySelector("#ocr-empty-message").hidden =
        Boolean(response.extracted_text?.trim());
      renderInformation(response.extracted_information);
      results.hidden = false;
    } catch (error) {
      setError(error instanceof Error ? error.message : "Report extraction failed.");
    } finally {
      extractLoading.hidden = true;
      extractButton.disabled = !fileInput.files?.length;
    }
  });
})();
