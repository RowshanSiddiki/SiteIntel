(() => {
  const form = document.querySelector("#project-settings-form");
  const root = document.querySelector("#project-workspace");
  if (!form || !root) return;
  const note = document.querySelector("#settings-message");
  const show = (value, isError) => {
    note.textContent = value;
    note.hidden = !value;
    note.classList.toggle("form-error", isError);
  };
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    show("", false);
    const body = Object.fromEntries(new FormData(form));
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(root.dataset.projectId)}`, {
        method: "PATCH",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "",
        },
        body: JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error?.message || "Could not save changes.");
      location.reload();
    } catch (failure) {
      show(failure.message, true);
      button.disabled = false;
    }
  });
})();
