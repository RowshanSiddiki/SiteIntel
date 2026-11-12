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
