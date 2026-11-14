(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  const formatNumber = (value) =>
    Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });

  const showPrediction = (model, prediction) => {
    if (model === "cement") {
      return `Predicted cement consumption: ${formatNumber(prediction.predicted_consumption_tonnes)} tonnes.`;
    }
    if (model === "concrete") {
      return `Predicted compressive strength: ${formatNumber(prediction.compressive_strength_mpa)} MPa.`;
    }
    return `Delay risk: ${prediction.delay_risk} (class ${prediction.predicted_class}); probability ${(
      Number(prediction.delay_probability) * 100
    ).toFixed(1)}%.`;
  };

  const projectSelect = document.querySelector("#prediction-project");
  const projectStatus = document.querySelector("#project-status");
  const projectError = document.querySelector("#project-error");
  const predictionList = document.querySelector("#project-predictions");
  const uploadLink = document.querySelector('a[href$="#my-projects"]');

  const request = async (url, options = {}) => {
    const response = await fetch(url, {
      ...options,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken || "" },
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.error?.message || "The request failed.");
    return payload;
  };
  const showError = (error) => {
    projectError.textContent = error instanceof Error ? error.message : "The request failed.";
    projectError.hidden = false;
  };
  const addText = (parent, tag, text) => {
    const element = document.createElement(tag);
    element.textContent = text;
    parent.append(element);
    return element;
  };
  const renderRecord = (record, editable, latest) => {
    const card = document.createElement("section");
    card.className = "ml-card";
    addText(card, "h2", record.model_name);
    addText(card, "p", `Document: ${record.document_name} · ${record.created_at || ""}`);
    addText(card, "p", `Status: ${record.status.replaceAll("_", " ")}${latest ? "" : " (previous)"}`);
    if (record.result) {
      const model = { cement_demand: "cement", concrete_strength: "concrete", delay_risk: "delay" }[record.model_key];
      addText(card, "p", showPrediction(model, record.result));
    } else if (record.status === "unavailable") {
      addText(card, "p", "This model could not produce a reliable prediction. Please try again later.");
    }
    const entries = Object.entries(record.inputs);
    if (entries.length) {
      addText(card, "h3", "Inputs used");
      const list = document.createElement("ul");
      for (const [name, item] of entries) {
        addText(list, "li", `${item.label} (${name}): ${item.value}${item.unit ? ` ${item.unit}` : ""} (${item.source === "ocr" ? "OCR" : "manually supplied"})`);
      }
      card.append(list);
    }
    if (record.missing.length) {
      addText(card, "h3", "Information needed");
      const form = document.createElement("form");
      for (const field of record.missing) {
        const label = document.createElement("label");
        label.textContent = `${field.label} (${field.name})${field.unit ? ` — ${field.unit}` : ""}. Example: ${field.example}. ${field.reason}`;
        let input;
        if (field.options) {
          input = document.createElement("select");
          input.add(new Option("Select an option", ""));
          for (const option of field.options) input.add(new Option(option, option));
        } else {
          input = document.createElement("input");
          input.type = "number";
          input.step = "any";
          if (field.name !== "avg_temp_c") input.min = "0";
        }
        input.name = field.name;
        input.required = true;
        input.disabled = !editable || !latest;
        label.append(input);
        form.append(label);
      }
      if (editable && latest) {
        const button = addText(form, "button", "Generate prediction");
        button.className = "button button-primary";
        button.type = "submit";
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          if (!form.reportValidity()) return;
          button.disabled = true;
          projectError.hidden = true;
          projectStatus.textContent = "Generating prediction…";
          const values = {};
          for (const field of record.missing) {
            const value = form.elements.namedItem(field.name).value;
            values[field.name] = field.options ? value : Number(value);
          }
          try {
            await request(`/api/predictions/${record.id}/complete`, {
              method: "POST", body: JSON.stringify(values),
            });
            await loadPredictions();
          } catch (error) {
            showError(error);
            projectStatus.textContent = "";
            button.disabled = false;
          }
        });
      } else if (!editable) {
        addText(form, "p", "An owner or editor can supply this information.");
      }
      card.append(form);
    }
    if (editable && latest && !record.missing.length) {
      const button = addText(card, "button", "Run prediction again");
      button.className = "button button-outline";
      button.type = "button";
      button.addEventListener("click", async () => {
        button.disabled = true;
        projectError.hidden = true;
        projectStatus.textContent = "Generating prediction…";
        try {
          await request(`/api/predictions/${record.id}/rerun`, { method: "POST" });
          await loadPredictions();
        } catch (error) {
          showError(error);
          projectStatus.textContent = "";
          button.disabled = false;
        }
      });
    }
    predictionList.append(card);
  };
  const loadPredictions = async () => {
    const projectId = projectSelect.value;
    predictionList.replaceChildren();
    projectError.hidden = true;
    if (!projectId) {
      projectStatus.textContent = "Select a project to see its prediction history.";
      return;
    }
    uploadLink.href = `/dashboard/projects/${encodeURIComponent(projectId)}/#project-reports`;
    projectStatus.textContent = "Loading predictions…";
    try {
      const payload = await request(`/api/projects/${encodeURIComponent(projectId)}/predictions`);
      if (projectSelect.value !== projectId) return;
      projectStatus.textContent = payload.predictions.length ? "" : "No predictions yet. Upload a report for this project.";
      const seen = new Set();
      for (const record of payload.predictions) {
        const key = `${record.document_id}:${record.model_key}`;
        renderRecord(record, payload.editable, !seen.has(key));
        seen.add(key);
      }
    } catch (error) {
      projectStatus.textContent = "";
      showError(error);
    }
  };
  const loadProjects = async (selected = "") => {
    const payload = await request("/api/projects");
    projectSelect.replaceChildren(new Option("Select a project", ""));
    for (const project of payload.projects) {
      projectSelect.add(new Option(project.name, String(project.id)));
    }
    projectSelect.value = selected;
    await loadPredictions();
  };
  projectSelect.addEventListener("change", loadPredictions);
  document.querySelector("#create-project-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    button.disabled = true;
    projectError.hidden = true;
    try {
      const payload = await request("/api/projects", {
        method: "POST", body: JSON.stringify({ name: form.elements.namedItem("name").value }),
      });
      location.assign(payload.workspace_url);
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  });
  loadProjects(new URLSearchParams(location.search).get("project_id") || "").catch(showError);

  for (const form of document.querySelectorAll(".ml-form")) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;

      const button = form.querySelector('button[type="submit"]');
      const result = form.querySelector(".ml-result");
      const error = form.querySelector(".ml-error");
      result.hidden = true;
      error.hidden = true;
      button.disabled = true;

      const values = Object.fromEntries(
        [...new FormData(form)].map(([name, value]) => {
          const input = form.elements.namedItem(name);
          return [
            name,
            input instanceof HTMLInputElement && input.type === "number"
              ? Number(value)
              : value,
          ];
        }),
      );

      try {
        const response = await fetch(form.dataset.endpoint, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken || "",
          },
          body: JSON.stringify(values),
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(
            payload?.error?.message || `Prediction failed (${response.status}).`,
          );
        }
        result.textContent = showPrediction(form.dataset.model, payload.prediction);
        result.hidden = false;
      } catch (requestError) {
        error.textContent =
          requestError instanceof Error
            ? requestError.message
            : "The prediction request failed.";
        error.hidden = false;
      } finally {
        button.disabled = false;
      }
    });
  }
})();
