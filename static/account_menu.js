(function () {
  "use strict";

  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function openDialog(dialog, trigger) {
    if (!dialog) return;
    dialog.__returnFocus = trigger || document.activeElement;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    const safeAction = dialog.querySelector("[autofocus], [data-account-dialog-close]");
    if (safeAction) safeAction.focus();
  }

  function closeDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  function enhanceAccountMenu(menu) {
    const summary = menu.querySelector("summary");
    const panel = menu.querySelector(".account-menu__panel");
    let closeTimer = null;

    function finishClose({ restoreFocus = false, onClosed = null } = {}) {
      if (closeTimer !== null) window.clearTimeout(closeTimer);
      closeTimer = null;
      menu.classList.remove("is-closing");
      menu.open = false;
      if (restoreFocus) summary.focus();
      if (onClosed) onClosed();
    }

    function closeMenu(options = {}) {
      if (!menu.open || menu.classList.contains("is-closing")) {
        if (options.onClosed) options.onClosed();
        return;
      }
      if (!panel || reducedMotion()) {
        finishClose(options);
        return;
      }
      menu.classList.add("is-closing");
      const finish = (event) => {
        if (event && (event.target !== panel || event.animationName !== "account-menu-out")) return;
        panel.removeEventListener("animationend", finish);
        finishClose(options);
      };
      panel.addEventListener("animationend", finish);
      closeTimer = window.setTimeout(() => finishClose(options), 210);
    }

    summary.addEventListener("click", (event) => {
      if (!menu.open || menu.classList.contains("is-closing")) return;
      event.preventDefault();
      closeMenu();
    });
    menu.addEventListener("toggle", () => {
      if (menu.open) menu.classList.remove("is-closing");
      summary.setAttribute("aria-expanded", String(menu.open));
    });
    menu.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !menu.open) return;
      event.preventDefault();
      closeMenu({ restoreFocus: true });
    });
    document.addEventListener("click", (event) => {
      if (menu.open && !menu.contains(event.target)) closeMenu();
    });
    menu.querySelectorAll("[data-account-dialog-trigger]").forEach((trigger) => {
      trigger.addEventListener("click", () => {
        const dialog = document.getElementById(trigger.getAttribute("aria-controls"));
        closeMenu({ onClosed: () => openDialog(dialog, summary) });
      });
    });
  }

  function enhanceAccountDialogs() {
    document.querySelectorAll("dialog[data-account-modal]").forEach((dialog) => {
      dialog.addEventListener("close", () => {
        if (dialog.__returnFocus && document.contains(dialog.__returnFocus)) dialog.__returnFocus.focus();
      });
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) closeDialog(dialog);
      });
      dialog.querySelectorAll("[data-account-dialog-close]").forEach((button) => {
        button.addEventListener("click", () => closeDialog(dialog));
      });
    });
    document.querySelectorAll("form[data-account-form]").forEach((form) => {
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

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("details.account-menu").forEach(enhanceAccountMenu);
    enhanceAccountDialogs();
  });
})();
