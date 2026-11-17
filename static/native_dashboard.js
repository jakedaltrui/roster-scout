(function () {
  "use strict";

  const roots = document.querySelectorAll("[data-native-dashboard]");
  if (!roots.length) return;

  function number(value, places) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return Number(value).toFixed(places);
  }

  function percent(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return `${(Number(value) * 100).toFixed(1)}%`;
  }

  function innings(outs) {
    if (outs === null || outs === undefined || Number.isNaN(Number(outs))) return "—";
    const parsed = Number(outs);
    return `${Math.floor(parsed / 3)}.${parsed % 3}`;
  }

  function metricText(key, value) {
    if (["strikeoutRate", "walkRate", "xbhRate"].includes(key)) return percent(value);
    if (["era", "kPer9", "bbPer9"].includes(key)) return number(value, 2);
    return number(value, 3);
  }

  function chartPoints(values) {
    const available = values.filter(function (value) { return Number.isFinite(value); });
    if (available.length < 2) return "";
    const maximum = Math.max(10, ...available);
    return values.map(function (value, index) {
      const x = 12 + index * (376 / Math.max(1, values.length - 1));
      const normalized = Number.isFinite(value) ? value / maximum : 0;
      const y = 142 - normalized * 120;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
  }

  function cell(text, className) {
    const element = document.createElement("td");
    element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function emptyRow(columns, message) {
    const row = document.createElement("tr");
    const value = cell(message, "dashboard-table__empty");
    value.colSpan = columns;
    row.appendChild(value);
    return row;
  }

  function renderHitting(root, leaders) {
    const body = root.querySelector("[data-hitting-leaders]");
    if (!body) return;
    body.replaceChildren();
    if (!leaders.length) {
      body.appendChild(emptyRow(6, "No hitting sample is available for this timeframe."));
      return;
    }
    leaders.forEach(function (player) {
      const row = document.createElement("tr");
      row.append(
        cell(player.name, "dashboard-table__player"),
        cell(player.position || "—"),
        cell(player.plateAppearances ?? "—"),
        cell(number(player.average, 3)),
        cell(player.homeRuns ?? "—"),
        cell(number(player.ops, 3), "dashboard-table__emphasis")
      );
      body.appendChild(row);
    });
  }

  function renderPitching(root, leaders) {
    const body = root.querySelector("[data-pitching-leaders]");
    if (!body) return;
    body.replaceChildren();
    if (!leaders.length) {
      body.appendChild(emptyRow(6, "No pitching sample is available for this timeframe."));
      return;
    }
    leaders.forEach(function (player) {
      const row = document.createElement("tr");
      row.append(
        cell(player.name, "dashboard-table__player"),
        cell(player.position || "—"),
        cell(innings(player.inningsOuts)),
        cell(number(player.era, 2), "dashboard-table__emphasis"),
        cell(number(player.whip, 3)),
        cell(number(player.kPer9, 2))
      );
      body.appendChild(row);
    });
  }

  function renderSplits(root, splits) {
    const container = root.querySelector("[data-dashboard-splits]");
    if (!container) return;
    container.replaceChildren();
    if (!splits.length) {
      const empty = document.createElement("p");
      empty.className = "dashboard-splits__empty";
      empty.textContent = "NCAA did not publish situational splits for this saved report.";
      container.appendChild(empty);
      return;
    }
    splits.forEach(function (split) {
      const item = document.createElement("article");
      item.className = "dashboard-split";
      const heading = document.createElement("div");
      const title = document.createElement("h4");
      const type = document.createElement("span");
      title.textContent = split.label;
      type.textContent = split.statType === "batting" ? "Hitting" : "Pitching";
      heading.append(title, type);
      const values = document.createElement("dl");
      [[split.primaryLabel, split.primaryValue], [split.secondaryLabel, split.secondaryValue]].forEach(function (entry) {
        const group = document.createElement("div");
        const term = document.createElement("dt");
        const value = document.createElement("dd");
        term.textContent = entry[0];
        value.textContent = entry[1] === null || entry[1] === undefined
          ? "—"
          : entry[0].includes("%")
            ? percent(entry[1])
            : number(entry[1], 3);
        group.append(term, value);
        values.appendChild(group);
      });
      item.append(heading, values);
      if (split.status !== "complete") item.dataset.state = split.status;
      container.appendChild(item);
    });
  }

  function updateTrend(root, games) {
    const scored = games.map(function (game) {
      return game.scored === null || game.scored === undefined ? Number.NaN : Number(game.scored);
    });
    const allowed = games.map(function (game) {
      return game.allowed === null || game.allowed === undefined ? Number.NaN : Number(game.allowed);
    });
    const hasScores = games.length > 1 && scored.every(Number.isFinite) && allowed.every(Number.isFinite);
    const scoredLine = root.querySelector('[data-chart-series="for"]');
    const allowedLine = root.querySelector('[data-chart-series="against"]');
    const summary = root.querySelector("[data-run-summary]");
    if (!hasScores) {
      if (scoredLine) scoredLine.setAttribute("points", "");
      if (allowedLine) allowedLine.setAttribute("points", "");
      if (summary) summary.textContent = "Completed game scoring is not available";
      return;
    }
    if (scoredLine) scoredLine.setAttribute("points", chartPoints(scored));
    if (allowedLine) allowedLine.setAttribute("points", chartPoints(allowed));
    if (summary) {
      const totalScored = scored.reduce(function (total, value) { return total + value; }, 0);
      const totalAllowed = allowed.reduce(function (total, value) { return total + value; }, 0);
      summary.textContent = `${totalScored} scored · ${totalAllowed} allowed over ${games.length} games`;
    }
  }

  function updateScope(root, dashboard, scopeKey) {
    const scope = dashboard.scopes[scopeKey] || dashboard.scopes.full_season;
    root.querySelectorAll("[data-dashboard-scope]").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.dashboardScope === scopeKey));
    });
    root.querySelectorAll("[data-dashboard-metric]").forEach(function (element) {
      const key = element.dataset.dashboardMetric;
      element.textContent = metricText(key, scope.metrics[key]);
    });
    root.querySelectorAll("[data-dashboard-rate]").forEach(function (element) {
      const key = element.dataset.dashboardRate;
      const value = scope.metrics[key];
      const label = element.querySelector("strong");
      const bar = element.querySelector(".dashboard-rate__track span");
      if (label) label.textContent = percent(value);
      if (bar) bar.style.width = value === null || value === undefined
        ? "0%"
        : `${Math.min(100, Math.max(0, Number(value) * 400))}%`;
    });
    const note = root.querySelector("[data-dashboard-scope-note]");
    if (note) {
      const gameCopy = scope.games === null || scope.games === undefined
        ? ""
        : ` · ${scope.games} ${scope.games === 1 ? "game" : "games"}`;
      note.textContent = `${scope.label}${gameCopy}`;
    }
    const quality = root.querySelector("[data-dashboard-quality]");
    if (quality) {
      quality.textContent = scope.status === "complete"
        ? "This view is complete for the saved snapshot."
        : scope.status === "partial"
          ? "Some statistics were not published for this timeframe."
          : "This timeframe was not available from the source.";
    }
    renderHitting(root, scope.hittingLeaders || []);
    renderPitching(root, scope.pitchingLeaders || []);
    const canvas = root.querySelector(".dashboard-native");
    if (canvas) {
      canvas.classList.remove("is-updating");
      window.requestAnimationFrame(function () { canvas.classList.add("is-updating"); });
    }
  }

  function activateTab(root, key, focus) {
    root.querySelectorAll("[data-dashboard-tab]").forEach(function (button) {
      const active = button.dataset.dashboardTab === key;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      if (active && focus) button.focus();
    });
    root.querySelectorAll("[data-dashboard-panel]").forEach(function (panel) {
      panel.hidden = panel.dataset.dashboardPanel !== key;
    });
  }

  roots.forEach(function (root) {
    const source = root.querySelector("[data-native-dashboard-data]");
    if (!source) return;
    let dashboard;
    try {
      dashboard = JSON.parse(source.textContent);
    } catch (_error) {
      return;
    }
    updateTrend(root, dashboard.recentGames || []);
    renderSplits(root, dashboard.splits || []);
    updateScope(root, dashboard, "full_season");

    root.addEventListener("click", function (event) {
      const scope = event.target.closest("[data-dashboard-scope]");
      if (scope) updateScope(root, dashboard, scope.dataset.dashboardScope);
      const tab = event.target.closest("[data-dashboard-tab]");
      if (tab) activateTab(root, tab.dataset.dashboardTab, false);
    });
    root.addEventListener("keydown", function (event) {
      const tab = event.target.closest("[data-dashboard-tab]");
      if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const tabs = Array.from(root.querySelectorAll("[data-dashboard-tab]"));
      const current = tabs.indexOf(tab);
      let next = current;
      if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
      if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      event.preventDefault();
      activateTab(root, tabs[next].dataset.dashboardTab, true);
    });
  });
})();
