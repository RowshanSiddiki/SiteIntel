(() => {
  const form = document.querySelector("#create-project");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button");
    const error = document.querySelector("#project-create-error");
    button.disabled = true;
    error.hidden = true;
    try {
      const response = await fetch("/api/projects", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "",
        },
        body: JSON.stringify({ name: form.elements.namedItem("name").value }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error?.message || "Could not create project.");
      location.assign(data.workspace_url);
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
      button.disabled = false;
    }
  });
})();
