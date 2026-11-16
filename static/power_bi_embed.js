(function () {
  "use strict";

  const roots = document.querySelectorAll("[data-power-bi-root]");
  if (!roots.length) return;

  const PAGE_NAMES = {
    Overview: "c3eb675dfc6a4399b6bd",
    Roster: "a1b2c3d4e5f60718293a",
    Hitting: "b2c3d4e5f60718293a4b",
    Pitching: "c3d4e5f60718293a4b5c",
    Fielding: "d4e5f60718293a4b5c6d",
    Splits: "e5f60718293a4b5c6d7e",
    "Player Detail": "f60718293a4b5c6d7e8f",
    "Data Quality": "0718293a4b5c6d7e8f90",
  };

  const SCOPE_FILTERS = {
    full_season: null,
    last_10: "last_10",
    conference: "conference",
  };

  function previewMarkup() {
    return `<div class="dashboard-preview" aria-label="Synthetic dashboard preview">
      <p class="dashboard-preview__notice">Development preview · Illustrative values only</p>
      <div class="dashboard-preview__metrics">
        <div><span>Team OPS</span><strong>.812</strong></div>
        <div><span>Strikeout rate</span><strong>18.4%</strong></div>
        <div><span>Staff WHIP</span><strong>1.29</strong></div>
        <div><span>Fielding</span><strong>.972</strong></div>
      </div>
      <div class="dashboard-preview__body">
        <div><h3>Lineup profile</h3><table><thead><tr><th>Player</th><th>OPS</th><th>ISO</th><th>BB%</th><th>K%</th></tr></thead><tbody><tr><td>Sample hitter</td><td>.948</td><td>.204</td><td>12.6%</td><td>16.8%</td></tr><tr><td>Sample hitter</td><td>.876</td><td>.181</td><td>10.2%</td><td>19.1%</td></tr><tr><td>Sample hitter</td><td>.821</td><td>.157</td><td>8.9%</td><td>14.7%</td></tr></tbody></table></div>
        <div><h3>Data quality</h3><ul class="dashboard-preview__quality"><li>Season statistics <span>Ready</span></li><li>Last 10 games <span>Ready</span></li><li>Conference games <span>Ready</span></li><li>Situational splits <span>Ready</span></li></ul></div>
      </div>
    </div>`;
  }

  function showMessage(root, title, detail) {
    const message = root.querySelector("[data-dashboard-message]");
    const stage = root.querySelector("[data-dashboard-stage]");
    if (message) {
      message.hidden = false;
      message.innerHTML = `<strong>${title}</strong><span>${detail}</span>`;
    }
    if (stage) stage.setAttribute("aria-busy", "false");
  }

  async function initialize(root) {
    if (root.dataset.dashboardInitialized === "true") return;
    if (root.dataset.dashboardState !== "ready") return;
    root.dataset.dashboardInitialized = "true";
    const container = root.querySelector("[data-power-bi-container]");
    const message = root.querySelector("[data-dashboard-message]");
    const stage = root.querySelector("[data-dashboard-stage]");
    let configuration;
    try {
      const response = await fetch(root.dataset.embedUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) throw new Error("embed unavailable");
      configuration = await response.json();
    } catch (_error) {
      showMessage(root, "The dashboard couldn’t open", "Your saved workbook is unaffected and remains available above.");
      root.dataset.dashboardInitialized = "false";
      return;
    }

    if (configuration.synthetic) {
      container.innerHTML = previewMarkup();
      container.hidden = false;
      container.classList.add("is-ready");
      message.hidden = true;
      stage.setAttribute("aria-busy", "false");
      root._rosterScoutReport = null;
      return;
    }

    if (!window.powerbi || !window["powerbi-client"]) {
      showMessage(root, "The dashboard couldn’t open", "Reload the page or use the saved workbook above.");
      return;
    }
    const models = window["powerbi-client"].models;
    const report = window.powerbi.embed(container, {
      type: "report",
      id: configuration.reportId,
      embedUrl: configuration.embedUrl,
      accessToken: configuration.accessToken,
      tokenType: models.TokenType.Embed,
      permissions: models.Permissions.Read,
      settings: {
        panes: { filters: { visible: false }, pageNavigation: { visible: false } },
        bars: { actionBar: { visible: false }, statusBar: { visible: false } },
        background: models.BackgroundType.Transparent,
      },
    });
    root._rosterScoutReport = report;
    report.on("loaded", function () {
      container.hidden = false;
      container.classList.add("is-ready");
      message.hidden = true;
      stage.setAttribute("aria-busy", "false");
    });
    report.on("error", function () {
      showMessage(root, "The dashboard couldn’t open", "Your saved workbook is unaffected and remains available above.");
    });

    const expiry = Date.parse(configuration.expiresAt);
    const renewalDelay = Math.max(30_000, expiry - Date.now() - 5 * 60_000);
    window.setTimeout(async function renew() {
      try {
        const response = await fetch(root.dataset.embedUrl, { credentials: "same-origin", cache: "no-store" });
        if (!response.ok) throw new Error("renewal failed");
        const next = await response.json();
        await report.setAccessToken(next.accessToken);
      } catch (_error) {
        showMessage(root, "Your dashboard session ended", "Reload this page to continue. Your saved workbook is still available.");
      }
    }, renewalDelay);
  }

  async function pollStatus(root) {
    if (root.dataset.dashboardState !== "pending") return;
    try {
      const response = await fetch(root.dataset.statusUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) throw new Error("status unavailable");
      const payload = await response.json();
      if (payload.status === "ready") {
        root.dataset.dashboardState = "ready";
        const chip = root.querySelector("[data-dashboard-status]");
        if (chip) { chip.dataset.state = "ready"; chip.textContent = "Ready"; }
        await initialize(root);
        return;
      }
      if (payload.status === "failed" || payload.status === "disabled") {
        root.dataset.dashboardState = payload.status;
        showMessage(
          root,
          payload.status === "failed" ? "The dashboard needs another try" : "Interactive analysis is not connected yet",
          "Your saved workbook is unaffected and remains available above."
        );
        return;
      }
    } catch (_error) {
      showMessage(root, "The dashboard is still being prepared", "Your saved workbook is ready above. Check back shortly.");
      return;
    }
    window.setTimeout(function () { pollStatus(root); }, 10_000);
  }

  roots.forEach(function (root) {
    const tabs = Array.from(root.querySelectorAll("[data-dashboard-page]"));
    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", async function () {
        tabs.forEach(function (item) {
          const selected = item === tab;
          item.setAttribute("aria-selected", String(selected));
          item.tabIndex = selected ? 0 : -1;
        });
        if (root._rosterScoutReport) {
          try { await root._rosterScoutReport.setPage(PAGE_NAMES[tab.dataset.dashboardPage]); } catch (_error) {}
        }
      });
      tab.addEventListener("keydown", function (event) {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        let next = index;
        if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
        if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = tabs.length - 1;
        tabs[next].focus();
        tabs[next].click();
      });
    });
    root.querySelectorAll("[data-dashboard-scope]").forEach(function (button) {
      button.addEventListener("click", async function () {
        root.querySelectorAll("[data-dashboard-scope]").forEach(function (item) {
          item.setAttribute("aria-pressed", String(item === button));
        });
        if (root._rosterScoutReport) {
          try {
            const selectedScope = SCOPE_FILTERS[button.dataset.dashboardScope];
            await root._rosterScoutReport.setFilters(selectedScope ? [{
              $schema: "http://powerbi.com/product/schema#basic",
              target: { table: "Scope", column: "scope_code" },
              operator: "In",
              values: [selectedScope],
              filterType: 1,
            }] : []);
          } catch (_error) {}
        }
        const previewNotice = root.querySelector(".dashboard-preview__notice");
        if (previewNotice) {
          previewNotice.textContent = `${button.textContent.trim()} · Development preview · Illustrative values only`;
        }
      });
    });
    initialize(root);
    pollStatus(root);
  });
})();
