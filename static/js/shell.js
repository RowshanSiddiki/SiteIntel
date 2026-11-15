(() => {
  const body = document.body;
  const toggle = document.querySelector(".nav-toggle");
  const scrim = document.querySelector(".app-scrim");
  const setNav = (open) => {
    body.classList.toggle("nav-open", open);
    if (toggle) toggle.setAttribute("aria-expanded", String(open));
    if (scrim) scrim.hidden = !open;
  };
  toggle?.addEventListener("click", () => setNav(!body.classList.contains("nav-open")));
  scrim?.addEventListener("click", () => setNav(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setNav(false);
      document.querySelectorAll("details.menu[open]").forEach((menu) => { menu.open = false; });
    }
  });

  // Only one header menu is open at a time, and clicking elsewhere closes it.
  document.addEventListener("click", (event) => {
    document.querySelectorAll("details.menu[open]").forEach((menu) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });

  document.querySelectorAll("[data-open-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = document.getElementById(button.dataset.openDialog);
      if (dialog && !dialog.open) { dialog.showModal(); dialog.querySelector("input")?.focus(); }
    });
  });
  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  });
})();
