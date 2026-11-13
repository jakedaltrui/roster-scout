(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.RosterScoutReportStatus = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const TIMEOUT_MS = 15 * 60 * 1000;
  const TERMINAL_STATES = new Set(["ready", "partial", "failed"]);

  function cadence(elapsed) {
    if (elapsed <= 30_000) return 3_000;
    if (elapsed <= 120_000) return 5_000;
    return 10_000;
  }

  function createReportStatusPoller(options) {
    const fetchStatus = options.fetchStatus;
    const now = options.now || Date.now;
    const schedule = options.schedule || setTimeout;
    const cancel = options.cancel || clearTimeout;
    const isHidden = options.isHidden || (() => false);
    const render = options.render || (() => {});
    const announce = options.announce || (() => {});
    const terminalReached = options.terminalReached || (() => {});
    let startedAt = null;
    let timer = null;
    let stopped = false;
    let terminal = false;
    let failures = 0;
    let lastState = null;

    function clearTimer() {
      if (timer !== null) cancel(timer);
      timer = null;
    }

    function queueNext() {
      clearTimer();
      if (stopped || terminal || isHidden()) return;
      timer = schedule(() => {
        timer = null;
        void checkNow();
      }, cadence(now() - startedAt));
    }

    async function checkNow(options = {}) {
      if (startedAt === null) startedAt = now();
      if (isHidden()) {
        clearTimer();
        return null;
      }
      if (!options.manual && now() - startedAt >= TIMEOUT_MS) {
        stopped = true;
        clearTimer();
        render({
          state: "still_working",
          heading: "Still working",
          message: "This is taking longer than expected. You can check again without starting another build.",
          terminal: true,
          sections: [],
        });
        return null;
      }
      try {
        const view = await fetchStatus();
        failures = 0;
        render(view);
        if (view.state !== lastState) {
          announce(`Refresh status changed to ${view.heading}.`);
          lastState = view.state;
        }
        terminal = Boolean(view.terminal) || TERMINAL_STATES.has(view.state);
        stopped = terminal;
        if (terminal) {
          clearTimer();
          terminalReached(view);
        }
        else queueNext();
        return view;
      } catch (_error) {
        failures += 1;
        if (failures >= 2) {
          render({ transient_error: "Status temporarily unavailable" });
        }
        queueNext();
        return null;
      }
    }

    async function start() {
      if (startedAt === null) startedAt = now();
      if (isHidden()) return null;
      return checkNow();
    }

    async function visibilityChanged() {
      if (isHidden()) {
        clearTimer();
        return null;
      }
      if (terminal) return null;
      stopped = false;
      return checkNow();
    }

    async function manualCheck() {
      terminal = false;
      stopped = false;
      failures = 0;
      startedAt = now();
      return checkNow({ manual: true });
    }

    return {
      start,
      checkNow,
      visibilityChanged,
      manualCheck,
      isStopped: () => stopped,
    };
  }

  function validStatusView(view) {
    return Boolean(
      view &&
      typeof view.state === "string" &&
      typeof view.heading === "string" &&
      typeof view.message === "string" &&
      typeof view.terminal === "boolean" &&
      Array.isArray(view.sections)
    );
  }

  function enhanceReportStatus(target) {
    if (!target || typeof target.getAttribute !== "function") return null;
    const statusUrl = target.getAttribute("data-report-status-url");
    if (!statusUrl || typeof fetch !== "function") return null;
    const heading = target.querySelector("[data-status-heading]");
    const message = target.querySelector("[data-status-message]");
    const sections = target.querySelector("[data-status-sections]");
    const note = target.querySelector("[data-status-note]");
    const live = target.querySelector("[data-status-live]");
    const checkButton = target.querySelector("[data-status-check]");

    function replaceSections(items) {
      if (!sections || typeof document === "undefined") return;
      const fragment = document.createDocumentFragment();
      items.forEach((item) => {
        const row = document.createElement("li");
        const name = document.createElement("strong");
        const state = document.createElement("span");
        name.textContent = String(item.name || "Section");
        state.textContent = item.explanation
          ? `${item.state} · ${item.explanation}`
          : String(item.state || "Waiting");
        row.append(name, state);
        fragment.append(row);
      });
      sections.replaceChildren(fragment);
    }

    function render(view) {
      if (view.transient_error) {
        if (note) note.textContent = view.transient_error;
        return;
      }
      if (heading) {
        heading.textContent = view.heading;
        if (view.state) heading.dataset.state = view.state;
      }
      if (message) message.textContent = view.message;
      if (note) note.textContent = "";
      replaceSections(view.sections || []);
      if (checkButton) checkButton.hidden = view.state !== "still_working";
    }

    const poller = createReportStatusPoller({
      async fetchStatus() {
        const response = await fetch(statusUrl, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error("status request failed");
        const view = await response.json();
        if (!validStatusView(view)) throw new Error("invalid status response");
        return view;
      },
      isHidden: () => Boolean(document.hidden),
      render,
      announce(copy) {
        if (live) live.textContent = copy;
      },
      terminalReached() {
        // Terminal responses change the saved snapshot, history, download, and
        // retry panels together. Reload the same scoped detail URL so the
        // server-rendered view replaces those regions atomically.
        window.location.reload();
      },
    });
    document.addEventListener("visibilitychange", () => {
      void poller.visibilityChanged();
    });
    if (checkButton) {
      checkButton.addEventListener("click", () => {
        checkButton.hidden = true;
        void poller.manualCheck();
      });
    }
    void poller.start();
    return poller;
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", () => {
      enhanceReportStatus(document.querySelector("[data-report-status-root]"));
    });
  }

  return { createReportStatusPoller, enhanceReportStatus };
});
