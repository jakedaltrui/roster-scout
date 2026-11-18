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

  const PREVIEW_SCOPE_DATA = {
    full_season: {
      metrics: [".812", "18.4%", "1.29", ".972"],
      runsFor: [6, 4, 8, 5, 7, 9, 3, 6, 8, 7],
      runsAgainst: [3, 5, 4, 2, 6, 3, 4, 5, 2, 4],
      runSummary: "63 scored · 38 allowed",
      rates: [
        { value: "11.2%", width: 45, benchmark: 34 },
        { value: "18.4%", width: 74, benchmark: 87 },
        { value: "10.9%", width: 44, benchmark: 36 },
      ],
    },
    last_10: {
      metrics: [".846", "17.8%", "1.18", ".976"],
      runsFor: [5, 7, 4, 9, 6, 8, 3, 7, 8, 6],
      runsAgainst: [4, 2, 5, 3, 4, 2, 3, 5, 2, 3],
      runSummary: "63 scored · 33 allowed",
      rates: [
        { value: "12.1%", width: 48, benchmark: 34 },
        { value: "17.8%", width: 71, benchmark: 87 },
        { value: "11.6%", width: 46, benchmark: 36 },
      ],
    },
    conference: {
      metrics: [".794", "19.1%", "1.34", ".969"],
      runsFor: [4, 6, 3, 7, 5, 4, 8, 3, 6, 5],
      runsAgainst: [5, 3, 4, 6, 2, 5, 3, 4, 5, 4],
      runSummary: "51 scored · 41 allowed",
      rates: [
        { value: "10.4%", width: 42, benchmark: 34 },
        { value: "19.1%", width: 76, benchmark: 87 },
        { value: "9.8%", width: 39, benchmark: 36 },
      ],
    },
  };

  function chartPoints(values) {
    return values.map(function (value, index) {
      const x = 12 + index * (376 / (values.length - 1));
      const y = 142 - value * 12;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
  }

  function previewMarkup() {
    return `<div class="dashboard-preview" aria-label="Synthetic dashboard preview">
      <p class="dashboard-preview__notice">Development preview · Illustrative values only</p>
      <div class="dashboard-preview__metrics">
        <div><span>Team OPS</span><strong data-preview-metric="0">.812</strong></div>
        <div><span>Strikeout rate</span><strong data-preview-metric="1">18.4%</strong></div>
        <div><span>Staff WHIP</span><strong data-preview-metric="2">1.29</strong></div>
        <div><span>Fielding</span><strong data-preview-metric="3">.972</strong></div>
      </div>
      <div class="dashboard-preview__charts">
        <figure class="dashboard-chart dashboard-chart--trend">
          <figcaption>
            <div><h3>Recent run trend</h3><p data-run-summary>63 scored · 38 allowed</p></div>
            <div class="dashboard-chart__legend" aria-hidden="true"><span data-series="for">Scored</span><span data-series="against">Allowed</span></div>
          </figcaption>
          <svg viewBox="0 0 400 170" role="img" aria-label="Runs scored and allowed across the ten most recent games">
            <g class="dashboard-chart__grid"><line x1="12" y1="22" x2="388" y2="22"/><line x1="12" y1="82" x2="388" y2="82"/><line x1="12" y1="142" x2="388" y2="142"/></g>
            <polyline class="dashboard-chart__line dashboard-chart__line--against" data-chart-series="against" points=""/>
            <polyline class="dashboard-chart__line dashboard-chart__line--for" data-chart-series="for" points=""/>
          </svg>
          <div class="dashboard-chart__axis" aria-hidden="true"><span>Game 1</span><span>Game 5</span><span>Game 10</span></div>
        </figure>
        <figure class="dashboard-chart dashboard-chart--rates">
          <figcaption><div><h3>Rate profile</h3><p>Team rate with division average marker</p></div></figcaption>
          <div class="dashboard-rate-list">
            <div class="dashboard-rate" data-preview-rate="0"><div><span>Walk rate</span><strong>11.2%</strong></div><div class="dashboard-rate__track"><span></span><i aria-hidden="true"></i></div></div>
            <div class="dashboard-rate" data-preview-rate="1"><div><span>Strikeout rate</span><strong>18.4%</strong></div><div class="dashboard-rate__track"><span></span><i aria-hidden="true"></i></div></div>
            <div class="dashboard-rate" data-preview-rate="2"><div><span>Extra-base hit rate</span><strong>10.9%</strong></div><div class="dashboard-rate__track"><span></span><i aria-hidden="true"></i></div></div>
          </div>
        </figure>
      </div>
      <p class="dashboard-preview__quality">Season, last 10, conference, and split data are ready.</p>
    </div>`;
  }

  function updatePreviewScope(root, scopeKey, scopeLabel) {
    const data = PREVIEW_SCOPE_DATA[scopeKey] || PREVIEW_SCOPE_DATA.full_season;
    const notice = root.querySelector(".dashboard-preview__notice");
    if (notice) notice.textContent = `${scopeLabel} · Development preview · Illustrative values only`;
    root.querySelectorAll("[data-preview-metric]").forEach(function (metric) {
      metric.textContent = data.metrics[Number(metric.dataset.previewMetric)];
    });
    const scored = root.querySelector('[data-chart-series="for"]');
    const allowed = root.querySelector('[data-chart-series="against"]');
    if (scored) scored.setAttribute("points", chartPoints(data.runsFor));
    if (allowed) allowed.setAttribute("points", chartPoints(data.runsAgainst));
    const summary = root.querySelector("[data-run-summary]");
    if (summary) summary.textContent = data.runSummary;
    root.querySelectorAll("[data-preview-rate]").forEach(function (rate) {
      const item = data.rates[Number(rate.dataset.previewRate)];
      rate.querySelector("strong").textContent = item.value;
      rate.style.setProperty("--rate-width", `${item.width}%`);
      rate.style.setProperty("--benchmark-position", `${item.benchmark}%`);
    });
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
      updatePreviewScope(root, "full_season", "Full season");
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
        updatePreviewScope(root, button.dataset.dashboardScope, button.textContent.trim());
      });
    });
    initialize(root);
    pollStatus(root);
  });
})();
