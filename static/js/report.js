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
  const modelUi = {
    workforce: {
      endpoint: "workforce",
      button: "#predict-workforce-button",
      modelStatus: "#workforce-model-status",
      message: "#workforce-readiness-message",
      features: "#workforce-required-fields",
      extracted: "#workforce-extracted-fields",
      missing: "#workforce-missing-fields",
      result: "#workforce-result",
      error: "#workforce-error",
      label: "Predict Workforce Attendance Risk",
    },
    supply_chain: {
      endpoint: "supply-chain",
      button: "#predict-supply-button",
      modelStatus: "#supply-model-status",
      message: "#supply-readiness-message",
      features: "#supply-required-fields",
      extracted: "#supply-extracted-fields",
      missing: "#supply-missing-fields",
      result: "#supply-result",
      error: "#supply-error",
      label: "Predict Supply Chain Stockout Risk",
    },
    construction_estimate: {
      endpoint: "construction-estimate",
      button: "#predict-construction-estimate-button",
      modelStatus: "#construction-estimate-model-status",
      message: "#construction-estimate-readiness-message",
      features: "#construction-estimate-required-fields",
      extracted: "#construction-estimate-extracted-fields",
      missing: "#construction-estimate-missing-fields",
      result: "#construction-estimate-result",
      error: "#construction-estimate-error",
      label: "Predict Construction Cost",
    },
    civil_risk: {
      endpoint: "civil-risk",
      button: "#predict-civil-risk-button",
      modelStatus: "#civil-risk-model-status",
      message: "#civil-risk-readiness-message",
      features: "#civil-risk-required-fields",
      extracted: "#civil-risk-extracted-fields",
      missing: "#civil-risk-missing-fields",
      result: "#civil-risk-result",
      error: "#civil-risk-error",
      label: "Predict Civil Project Risk",
    },
    cement_demand: {
      endpoint: "cement-demand",
      button: "#predict-cement-demand-button",
      modelStatus: "#cement-demand-model-status",
      message: "#cement-demand-readiness-message",
      features: "#cement-demand-required-fields",
      extracted: "#cement-demand-extracted-fields",
      missing: "#cement-demand-missing-fields",
      result: "#cement-demand-result",
      error: "#cement-demand-error",
      label: "Predict Cement Demand",
    },
  };

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

  const renderReadiness = (key, status) => {
    const config = modelUi[key];
    if (!config) return;
    const button = document.querySelector(config.button);
    const modelStatus = document.querySelector(config.modelStatus);
    const message = document.querySelector(config.message);
    const featuresList = document.querySelector(config.features);
    const extractedList = document.querySelector(config.extracted);
    const missingList = document.querySelector(config.missing);
    const result = document.querySelector(config.result);
    const error = document.querySelector(config.error);
    if (!button || !message || !missingList || !result) {
      if (button) button.disabled = true;
      if (message) {
        message.textContent =
          "The model card is incomplete. Reload the dashboard and try again.";
      }
      return;
    }
    status ||= {
      data_ready: false,
      missing_fields: [],
      required_features: [],
      available_inputs: {},
      model_available: false,
      model_loaded: false,
      model_issue:
        "The extraction response did not include readiness for this model.",
      prediction_enabled: false,
    };
    result.hidden = true;
    result.textContent = "";
    if (error) {
      error.hidden = true;
      error.textContent = "";
    }
    missingList.replaceChildren();
    if (featuresList) {
      featuresList.replaceChildren();
      for (const feature of status.required_features || []) {
        const item = document.createElement("li");
        item.textContent = feature;
        featuresList.append(item);
      }
    }
    if (extractedList) {
      extractedList.replaceChildren();
      const inputs = status.available_inputs || {};
      for (const [feature, value] of Object.entries(inputs)) {
        const item = document.createElement("li");
        item.textContent = `${feature}: ${String(value)}`;
        extractedList.append(item);
      }
      if (!Object.keys(inputs).length) {
        const item = document.createElement("li");
        item.textContent = "No model input values were extracted.";
        extractedList.append(item);
      }
    }

    if (modelStatus) {
      const modelFile = status.model_file || key;
      if (status.model_available) {
        modelStatus.textContent = `Model loaded and ready: ${modelFile}`;
      } else if (status.model_loaded) {
        modelStatus.textContent = `Model loaded, but prediction is unavailable: ${status.model_issue || "model validation failed"}`;
      } else {
        modelStatus.textContent = `Model unavailable: ${status.model_issue || modelFile}`;
      }
    }

    message.replaceChildren();
    if (!status.data_ready) {
      message.append("More report information is needed for this model.");
      for (const field of status.missing_fields || []) {
        const item = document.createElement("li");
        item.textContent = field.startsWith("one of:")
          ? field
          : fieldLabel(field);
        missingList.append(item);
      }
    } else {
      message.append("The report contains the required information.");
    }
    if (status.data_issue || status.model_warning) {
      const dataIssue = document.createElement("p");
      dataIssue.className = "report-data-warning";
      dataIssue.textContent = [status.data_issue, status.model_warning]
        .filter(Boolean)
        .join(" ");
      message.append(dataIssue);
    }
    button.disabled = !status.prediction_enabled;
    button.dataset.model = key;
  };

  const displayPrediction = (element, modelKey, response) => {
    const prediction = response.prediction;
    if (modelKey === "workforce") {
      element.textContent = `Predicted workforce attendance risk: ${prediction}.`;
    } else if (modelKey === "supply_chain") {
      const label =
        Number(prediction) === 1
          ? "Stockout detected (1)."
          : "No stockout detected (0).";
      element.textContent = label;
    } else if (modelKey === "construction_estimate") {
      const estimate = Number(prediction);
      element.textContent = Number.isFinite(estimate)
        ? `Estimated Total_Estimate: ${estimate.toLocaleString(undefined, {
            maximumFractionDigits: 2,
          })} (dataset unit not specified).`
        : "The model returned an invalid cost estimate.";
    } else if (modelKey === "civil_risk") {
      const label =
        Number(prediction) === 1 ? "Good Project (1)." : "Not Good Project (0).";
      const probabilities = response.class_probabilities;
      const probabilityText =
        probabilities && Object.keys(probabilities).length
          ? ` Class probabilities — ${Object.entries(probabilities)
              .map(
                ([classLabel, value]) =>
                  `${classLabel}: ${(Number(value) * 100).toFixed(1)}%`,
              )
              .join(", ")}.`
          : "";
      element.textContent = `${label}${probabilityText}`;
    } else if (modelKey === "cement_demand") {
      const estimate = Number(prediction);
      element.textContent = Number.isFinite(estimate)
        ? `Estimated consumption: ${estimate.toLocaleString(undefined, {
            maximumFractionDigits: 2,
          })} tonnes.`
        : "The model returned an invalid cement-demand estimate.";
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
      for (const modelKey of Object.keys(modelUi)) {
        renderReadiness(modelKey, readiness[modelKey]);
      }
      results.hidden = false;
    } catch (error) {
      setError(error instanceof Error ? error.message : "Report extraction failed.");
    } finally {
      extractLoading.hidden = true;
      extractButton.disabled = !fileInput.files?.length;
    }
  });

  const runPrediction = async (modelKey) => {
    if (!reportId || !readiness?.[modelKey]?.prediction_enabled) return;
    const config = modelUi[modelKey];
    const button = document.querySelector(config.button);
    const result = document.querySelector(config.result);
    const error = document.querySelector(config.error);
    if (!button || !result) return;
    button.disabled = true;
    button.textContent = "Predicting…";
    if (error) {
      error.hidden = true;
      error.textContent = "";
    }
    result.hidden = true;

    try {
      const response = await apiRequest(
        `/api/reports/${encodeURIComponent(reportId)}/predict/${config.endpoint}`,
        { method: "POST" },
      );
      displayPrediction(result, modelKey, response);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Prediction failed.";
      const errorElement = document.querySelector(config.error);
      if (errorElement) {
        errorElement.textContent = message;
        errorElement.hidden = false;
      } else {
        setError(message);
      }
    } finally {
      button.textContent = config.label;
      button.disabled = !readiness?.[modelKey]?.prediction_enabled;
    }
  };

  for (const [modelKey, config] of Object.entries(modelUi)) {
    const button = document.querySelector(config.button);
    button?.addEventListener("click", () => runPrediction(modelKey));
  }
})();
