(function () {
  "use strict";

  function preferredTheme() {
    try {
      const stored = localStorage.getItem("roster-scout-theme");
      if (stored === "light" || stored === "dark") return stored;
    } catch (_error) {
      // Storage is optional; device preference remains a complete fallback.
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function applyTheme(theme, control) {
    document.documentElement.dataset.theme = theme;
    if (control) {
      const next = theme === "dark" ? "light" : "dark";
      control.textContent = `Switch to ${next} mode`;
      control.setAttribute("aria-label", `Switch to ${next} mode`);
    }
  }

  function openDialog(dialog, trigger) {
    if (!dialog) return;
    dialog.__returnFocus = trigger || document.activeElement;
    if (typeof dialog.showModal === "function") {
      if (dialog.open) dialog.close();
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    const safeAction = dialog.querySelector("[autofocus], [data-dialog-close]");
    if (safeAction) safeAction.focus();
  }

  function closeDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  function enhanceDialogs() {
    document.querySelectorAll("[data-dialog-trigger]").forEach((trigger) => {
      trigger.addEventListener("click", () => {
        openDialog(document.getElementById(trigger.getAttribute("aria-controls")), trigger);
      });
    });
    document.querySelectorAll("dialog[data-modal]").forEach((dialog) => {
      dialog.addEventListener("close", () => {
        if (dialog.__returnFocus && document.contains(dialog.__returnFocus)) {
          dialog.__returnFocus.focus();
        }
      });
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) closeDialog(dialog);
      });
      dialog.querySelectorAll("[data-dialog-close]").forEach((button) => {
        button.addEventListener("click", () => closeDialog(dialog));
      });
      if (dialog.hasAttribute("data-auto-modal")) openDialog(dialog, null);
    });
  }

  function enhanceRoleConfirmations() {
    document.querySelectorAll("form[data-role-form]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        const select = form.querySelector('select[name="role"]');
        const requiresConfirmation = form.dataset.originalRole === "admin"
          && select && select.value === "member" && !form.dataset.confirmed;
        if (!requiresConfirmation) return;
        event.preventDefault();
        openDialog(document.getElementById(form.dataset.confirmDialog), event.submitter);
      });
    });
    document.querySelectorAll("[data-confirm-submit]").forEach((button) => {
      button.addEventListener("click", () => {
        const form = document.getElementById(button.dataset.confirmSubmit);
        if (!form) return;
        form.dataset.confirmed = "true";
        closeDialog(button.closest("dialog"));
        form.requestSubmit();
      });
    });
  }

  function enhancePendingForms() {
    document.querySelectorAll("form").forEach((form) => {
      form.addEventListener("submit", (event) => {
        if (event.defaultPrevented) return;
        const button = event.submitter;
        if (!button || !button.dataset.pendingLabel) return;
        button.disabled = true;
        button.textContent = button.dataset.pendingLabel;
        form.setAttribute("aria-busy", "true");
      });
    });
  }

  function enhanceAccountMenu() {
    const menu = document.querySelector("details.account-menu");
    if (!menu) return;
    const summary = menu.querySelector("summary");
    menu.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !menu.open) return;
      event.preventDefault();
      menu.open = false;
      summary.focus();
    });
    menu.addEventListener("toggle", () => {
      summary.setAttribute("aria-expanded", String(menu.open));
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const control = document.querySelector("[data-theme-control]");
    if (control) {
      applyTheme(preferredTheme(), control);
      control.addEventListener("click", () => {
        const next = document.documentElement.dataset.theme === "dark"
          ? "light"
          : "dark";
        applyTheme(next, control);
        try {
          localStorage.setItem("roster-scout-theme", next);
        } catch (_error) {
          // Theme switching remains functional when storage is unavailable.
        }
      });
    }
    enhanceDialogs();
    enhanceRoleConfirmations();
    enhancePendingForms();
    enhanceAccountMenu();
  });
})();
