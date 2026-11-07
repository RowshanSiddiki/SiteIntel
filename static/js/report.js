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
  let reportId = null;
  let readiness = null;

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
      throw new Error(
        payload?.error?.message || `The request failed (${response.status}).`,
      );
    }
    return payload;
  };

  const renderInformation = (groups) => {
    const container = document.querySelector("#extracted-information");
    container.replaceChildren();
    const groupLabels = {
      report: "Report",
      workforce: "Workforce",
      supply_chain: "Supply chain",
    };

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

  const renderReadiness = (key, status, buttonSelector, messageSelector, listSelector, resultSelector) => {
    const button = document.querySelector(buttonSelector);
    const message = document.querySelector(messageSelector);
    const missingList = document.querySelector(listSelector);
    const result = document.querySelector(resultSelector);
    result.hidden = true;
    result.textContent = "";
    missingList.replaceChildren();

    if (!status.data_ready) {
      message.textContent = "More report information is needed for this model.";
      for (const field of status.missing_fields || []) {
        const item = document.createElement("li");
        item.textContent = field.startsWith("one of:")
          ? field
          : fieldLabel(field);
        missingList.append(item);
      }
    } else {
      message.textContent = "The report contains the required information.";
    }
    if (status.data_issue) {
      const dataIssue = document.createElement("p");
      dataIssue.className = "report-data-warning";
      dataIssue.textContent = status.data_issue;
      message.append(dataIssue);
    }
    if (!status.model_available && status.model_issue) {
      const modelIssue = document.createElement("p");
      modelIssue.className = "model-availability-warning";
      modelIssue.textContent = `Prediction is unavailable: ${status.model_issue}`;
      message.append(modelIssue);
    }
    button.disabled = !status.prediction_enabled;
    button.dataset.model = key;
  };

  const displayPrediction = (element, modelKey, prediction) => {
    if (modelKey === "workforce") {
      element.textContent = `Predicted workforce attendance risk: ${prediction}.`;
    } else {
      const label =
        Number(prediction) === 1
          ? "Stockout detected (1)."
          : "No stockout detected (0).";
      element.textContent = label;
    }
    element.hidden = false;
  };

  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    reportId = null;
    readiness = null;
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
      reportId = response.report_id;
      readiness = response.readiness;
      document.querySelector("#original-extracted-text").textContent =
        response.extracted_text || "";
      document.querySelector("#ocr-empty-message").hidden =
        Boolean(response.extracted_text?.trim());
      renderInformation(response.extracted_information);
      renderReadiness(
        "workforce",
        readiness.workforce,
        "#predict-workforce-button",
        "#workforce-readiness-message",
        "#workforce-missing-fields",
        "#workforce-result",
      );
      renderReadiness(
        "supply_chain",
        readiness.supply_chain,
        "#predict-supply-button",
        "#supply-readiness-message",
        "#supply-missing-fields",
        "#supply-result",
      );
      results.hidden = false;
    } catch (error) {
      setError(error instanceof Error ? error.message : "Report extraction failed.");
    } finally {
      extractLoading.hidden = true;
      extractButton.disabled = !fileInput.files?.length;
    }
  });

  const runPrediction = async (modelKey, buttonSelector, resultSelector) => {
    if (!reportId || !readiness?.[modelKey]?.prediction_enabled) return;
    const button = document.querySelector(buttonSelector);
    const result = document.querySelector(resultSelector);
    button.disabled = true;
    button.textContent = "Predicting…";
    setError("");
    result.hidden = true;

    const endpointModel = modelKey === "supply_chain" ? "supply-chain" : "workforce";
    try {
      const response = await apiRequest(
        `/api/reports/${encodeURIComponent(reportId)}/predict/${endpointModel}`,
        { method: "POST" },
      );
      displayPrediction(result, modelKey, response.prediction);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Prediction failed.");
    } finally {
      button.textContent =
        modelKey === "workforce"
          ? "Predict Workforce Attendance Risk"
          : "Predict Supply Chain Stockout Risk";
      button.disabled = !readiness?.[modelKey]?.prediction_enabled;
    }
  };

  document
    .querySelector("#predict-workforce-button")
    .addEventListener("click", () =>
      runPrediction("workforce", "#predict-workforce-button", "#workforce-result"),
    );
  document
    .querySelector("#predict-supply-button")
    .addEventListener("click", () =>
      runPrediction("supply_chain", "#predict-supply-button", "#supply-result"),
    );
})();
