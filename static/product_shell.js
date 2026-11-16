(function () {
  "use strict";

  function preferredTheme() {
    try {
      const stored = localStorage.getItem("roster-scout-theme");
      if (stored === "light" || stored === "dark") return stored;
    } catch (_error) {
      // Storage is optional; device preference remains a complete fallback.
    }
    return "light";
  }

  function applyTheme(theme, control) {
    document.documentElement.dataset.theme = theme;
    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) themeColor.content = theme === "dark" ? "#09090b" : "#f6f6f3";
    if (control) {
      const next = theme === "dark" ? "light" : "dark";
      control.setAttribute("aria-label", `Switch to ${next} mode`);
      control.title = `Switch to ${next} mode`;
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

  function enhanceGoogleSheetsExports() {
    document.querySelectorAll("[data-google-sheets-confirm]").forEach((button) => {
      button.addEventListener("click", async () => {
        const dialog = button.closest("dialog");
        const trigger = dialog && dialog.__returnFocus;
        const form = trigger && trigger.closest("form[data-authenticated-snapshot-download]");
        if (!form) return;
        const oauthUrl = trigger.dataset.googleSheetsOauthUrl;
        if (oauthUrl) {
          const csrf = form.querySelector('input[name="csrf_token"]');
          if (!csrf) return;
          closeDialog(dialog);
          button.disabled = true;
          button.textContent = "Connecting…";
          const status = form.querySelector("[data-download-status]");
          try {
            const body = new FormData();
            body.append("csrf_token", csrf.value);
            const response = await fetch(oauthUrl, {
              method: "POST",
              credentials: "same-origin",
              headers: { Accept: "application/json" },
              body,
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || typeof payload.authorization_url !== "string") {
              throw new Error("Google authorization is unavailable.");
            }
            window.location.assign(payload.authorization_url);
          } catch (_error) {
            button.disabled = false;
            button.textContent = "Continue with Google";
            if (status) {
              status.textContent = "Couldn’t connect to Google. Try again.";
              status.focus();
            }
          }
          return;
        }
        window.open("https://sheets.new", "_blank", "noopener");
        closeDialog(dialog);
        form.dataset.exportTarget = "google-sheets";
        form.requestSubmit();
        delete form.dataset.exportTarget;
      });
    });
  }

  function enhanceAccountMenu() {
    const menu = document.querySelector("details.account-menu");
    if (!menu) return;
    const summary = menu.querySelector("summary");
    const panel = menu.querySelector(".account-menu__panel");
    let closeTimer = null;

    function finishClose(restoreFocus = false) {
      if (closeTimer !== null) window.clearTimeout(closeTimer);
      closeTimer = null;
      menu.classList.remove("is-closing");
      menu.open = false;
      if (restoreFocus) summary.focus();
    }

    function closeMenu({ restoreFocus = false } = {}) {
      if (!menu.open || menu.classList.contains("is-closing")) return;
      if (
        !panel
        || window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ) {
        finishClose(restoreFocus);
        return;
      }
      menu.classList.add("is-closing");
      const finish = (event) => {
        if (
          event
          && (event.target !== panel || event.animationName !== "product-menu-out")
        ) return;
        panel.removeEventListener("animationend", finish);
        finishClose(restoreFocus);
      };
      panel.addEventListener("animationend", finish);
      closeTimer = window.setTimeout(() => finishClose(restoreFocus), 220);
    }

    summary.addEventListener("click", (event) => {
      if (!menu.open || menu.classList.contains("is-closing")) return;
      event.preventDefault();
      closeMenu();
    });
    menu.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !menu.open) return;
      event.preventDefault();
      closeMenu({ restoreFocus: true });
    });
    menu.addEventListener("toggle", () => {
      if (menu.open) menu.classList.remove("is-closing");
      summary.setAttribute("aria-expanded", String(menu.open));
    });
    document.addEventListener("click", (event) => {
      if (menu.open && !menu.contains(event.target)) closeMenu();
    });
  }

  function enhanceTeamLogos() {
    document.querySelectorAll("[data-team-logo]").forEach((logo) => {
      const image = logo.querySelector("[data-team-logo-image]");
      const fallback = logo.querySelector("[data-team-logo-fallback]");
      if (!image) return;
      const showImage = () => {
        image.classList.add("is-loaded");
        if (fallback) fallback.hidden = true;
      };
      if (image.complete && image.naturalWidth > 0) showImage();
      else image.addEventListener("load", showImage, { once: true });
      image.addEventListener("error", () => image.remove(), { once: true });
    });
  }

  function enhanceReportRows() {
    document.querySelectorAll("[data-report-href]").forEach((row) => {
      row.addEventListener("click", (event) => {
        if (event.defaultPrevented || event.button > 0) return;
        if (event.target.closest("a, button, input, select, textarea, summary")) return;
        const destination = row.dataset.reportHref;
        if (destination) window.location.assign(destination);
      });
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
    enhanceGoogleSheetsExports();
    enhanceRoleConfirmations();
    enhancePendingForms();
    enhanceAccountMenu();
    enhanceTeamLogos();
    enhanceReportRows();
  });
})();
