(() => {
  const form = document.querySelector("[data-auth-form]");
  if (!form) return;

  const isRegister = form.dataset.register === "true";
  const fullName = form.querySelector("#full_name");
  const email = form.querySelector("#email");
  const password = form.querySelector("#password");
  const confirmation = form.querySelector("#password_confirmation");
  const message = form.querySelector("[data-validation-message]");

  const validPassword = (value) => {
    if (value.length < 12 || value.length > 128) return false;
    const kinds = [
      /[a-z]/.test(value),
      /[A-Z]/.test(value),
      /[0-9]/.test(value),
      /[^A-Za-z0-9]/.test(value),
    ];
    return kinds.filter(Boolean).length >= 3;
  };

  form.addEventListener("submit", (event) => {
    message.textContent = "";
    if (isRegister && fullName.value.trim().length < 2) {
      event.preventDefault();
      message.textContent = "Enter your full name (at least 2 characters).";
      fullName.focus();
      return;
    }
    if (!email.value.trim() || !email.validity.valid) {
      event.preventDefault();
      message.textContent = "Enter a valid email address.";
      email.focus();
      return;
    }
    if (!password.value || password.value.length > 128) {
      event.preventDefault();
      message.textContent = "Enter a valid password.";
      password.focus();
      return;
    }
    if (isRegister && !validPassword(password.value)) {
      event.preventDefault();
      message.textContent =
        "Use 12–128 characters and at least three character types.";
      password.focus();
      return;
    }
    if (isRegister && password.value !== confirmation.value) {
      event.preventDefault();
      message.textContent = "Passwords do not match.";
      confirmation.focus();
    }
  });
})();
