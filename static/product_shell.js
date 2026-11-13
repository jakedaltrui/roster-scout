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

  document.addEventListener("DOMContentLoaded", () => {
    const control = document.querySelector("[data-theme-control]");
    if (!control) return;
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
  });
})();
