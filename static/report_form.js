(() => {
  "use strict";

  const form = document.getElementById("report-create-form");
  if (!form) return;

  const divisionInput = document.getElementById("report-division");
  const seasonTeamInput = document.getElementById("season-team-id");
  const combobox = document.getElementById("team-combobox");
  const results = document.getElementById("team-results");
  const catalogStatus = document.getElementById("catalog-status");
  const exactSelection = document.getElementById("exact-selection");
  const retryButton = document.getElementById("catalog-retry");
  const submitButton = document.getElementById("create-report-submit");
  const backLink = document.getElementById("back-to-reports");
  const submitStatus = document.getElementById("submit-status");
  const divisionButtons = Array.from(
    document.querySelectorAll(".division-button")
  );
  const catalogCache = new Map();
  let requestSequence = 0;
  let catalog = [];
  let visibleTeams = [];
  let activeIndex = -1;
  let selectedTeam = null;
  let catalogYear = null;
  let submitting = false;

  function teamLabel(team) {
    return String(team.display_name || team.name || "").trim();
  }

  function closeResults() {
    results.hidden = true;
    combobox.setAttribute("aria-expanded", "false");
    combobox.removeAttribute("aria-activedescendant");
    activeIndex = -1;
  }

  function clearSelection() {
    selectedTeam = null;
    seasonTeamInput.value = "";
    exactSelection.hidden = true;
    exactSelection.textContent = "";
    submitButton.disabled = true;
  }

  function initials(label) {
    const words = label.match(/[\p{L}\p{N}]+/gu) || [];
    return words.slice(0, 2).map((word) => word[0]).join("").toUpperCase() || "•";
  }

  function teamOption(team, index) {
    const option = document.createElement("button");
    option.type = "button";
    option.id = `report-team-option-${index}`;
    option.className = "team-option";
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", "false");

    const logo = document.createElement("span");
    logo.className = "team-option__logo";
    logo.setAttribute("aria-hidden", "true");
    const fallback = document.createElement("span");
    fallback.textContent = initials(teamLabel(team));
    logo.appendChild(fallback);
    const teamId = String(team.team_id || "");
    if (/^\d+$/.test(teamId)) {
      const image = document.createElement("img");
      image.alt = "";
      image.loading = "lazy";
      image.addEventListener("load", () => { fallback.hidden = true; });
      image.addEventListener("error", () => { image.remove(); });
      image.src = `/api/team-logo/${encodeURIComponent(teamId)}`;
      logo.appendChild(image);
    }
    option.appendChild(logo);

    const copy = document.createElement("span");
    copy.className = "team-option__copy";
    const name = document.createElement("span");
    name.textContent = teamLabel(team);
    copy.appendChild(name);
    if (team.disambiguation) {
      const detail = document.createElement("span");
      detail.className = "team-option__meta";
      detail.textContent = String(team.disambiguation);
      copy.appendChild(detail);
    }
    option.appendChild(copy);
    option.addEventListener("mousedown", (event) => event.preventDefault());
    option.addEventListener("click", () => selectTeam(team));
    return option;
  }

  function renderResults() {
    const query = combobox.value.trim().toLocaleLowerCase();
    results.replaceChildren();
    activeIndex = -1;
    visibleTeams = catalog
      .filter((team) => {
        const label = teamLabel(team).toLocaleLowerCase();
        const rawName = String(team.name || "").toLocaleLowerCase();
        return label.includes(query) || rawName.includes(query);
      })
      .sort((left, right) => {
        const leftName = teamLabel(left).toLocaleLowerCase();
        const rightName = teamLabel(right).toLocaleLowerCase();
        const leftRank = leftName === query ? 0 : leftName.startsWith(query) ? 1 : 2;
        const rightRank = rightName === query ? 0 : rightName.startsWith(query) ? 1 : 2;
        return leftRank - rightRank || leftName.localeCompare(rightName);
      })
      .slice(0, 60);

    if (!visibleTeams.length) {
      const empty = document.createElement("p");
      empty.className = "selector-status";
      empty.textContent = "No matching NCAA teams";
      results.appendChild(empty);
    } else {
      visibleTeams.forEach((team, index) => {
        results.appendChild(teamOption(team, index));
      });
    }
    results.hidden = false;
    combobox.setAttribute("aria-expanded", "true");
  }

  function updateActive(nextIndex) {
    const options = Array.from(results.querySelectorAll(".team-option"));
    if (!options.length) return;
    activeIndex = Math.max(0, Math.min(nextIndex, options.length - 1));
    options.forEach((option, index) => {
      option.setAttribute("aria-selected", String(index === activeIndex));
    });
    const active = options[activeIndex];
    combobox.setAttribute("aria-activedescendant", active.id);
    active.scrollIntoView({ block: "nearest" });
  }

  function selectTeam(team) {
    const seasonTeamId = String(team.season_team_id || "");
    if (!/^\d+$/.test(seasonTeamId)) return;
    selectedTeam = team;
    combobox.value = teamLabel(team);
    combobox.setAttribute("aria-invalid", "false");
    seasonTeamInput.value = seasonTeamId;
    submitButton.disabled = false;
    exactSelection.textContent = `Selected: ${teamLabel(team)} · ${divisionInput.value.toUpperCase()} · ${catalogYear || team.year}`;
    exactSelection.hidden = false;
    catalogStatus.textContent = "Exact NCAA team selected.";
    closeResults();
  }

  async function loadCatalog(division) {
    const requestId = ++requestSequence;
    catalog = [];
    catalogYear = null;
    clearSelection();
    closeResults();
    combobox.disabled = true;
    combobox.value = "";
    combobox.placeholder = `Type a ${division.toUpperCase()} team name…`;
    catalogStatus.textContent = "Loading the current NCAA catalog…";
    retryButton.hidden = true;

    try {
      let data = catalogCache.get(division);
      if (!data) {
        const response = await fetch(
          `/api/teams?division=${encodeURIComponent(division)}`,
          { headers: { Accept: "application/json" }, credentials: "same-origin" }
        );
        data = await response.json();
        if (!response.ok || !Array.isArray(data.teams)) throw new Error("catalog");
        catalogCache.set(division, data);
      }
      if (requestId !== requestSequence) return;
      catalog = data.teams.filter((team) => (
        team && team.division === division && /^\d+$/.test(String(team.season_team_id || ""))
      ));
      catalogYear = data.year;
      combobox.disabled = false;
      catalogStatus.textContent = `${catalog.length} ${division.toUpperCase()} teams · ${catalogYear || "current season"}`;
    } catch (_error) {
      if (requestId !== requestSequence) return;
      combobox.disabled = true;
      catalogStatus.textContent = "We couldn’t load NCAA teams. Try again.";
      retryButton.hidden = false;
    }
  }

  combobox.addEventListener("input", () => {
    clearSelection();
    combobox.setAttribute("aria-invalid", "false");
    catalogStatus.textContent = "Choose an exact NCAA team from the results.";
    renderResults();
  });
  combobox.addEventListener("focus", () => {
    if (!combobox.disabled) renderResults();
  });
  combobox.addEventListener("click", () => {
    if (!combobox.disabled && results.hidden) renderResults();
  });
  combobox.addEventListener("blur", () => {
    window.setTimeout(closeResults, 120);
  });
  combobox.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (results.hidden) renderResults();
      updateActive(activeIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (results.hidden) renderResults();
      updateActive(activeIndex < 0 ? visibleTeams.length - 1 : activeIndex - 1);
    } else if (event.key === "Home" && !results.hidden) {
      event.preventDefault();
      updateActive(0);
    } else if (event.key === "End" && !results.hidden) {
      event.preventDefault();
      updateActive(visibleTeams.length - 1);
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      selectTeam(visibleTeams[activeIndex]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeResults();
    }
  });

  divisionButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const division = button.dataset.division;
      if (!division || division === divisionInput.value) return;
      divisionInput.value = division;
      divisionButtons.forEach((candidate) => {
        candidate.setAttribute(
          "aria-pressed",
          String(candidate === button)
        );
      });
      loadCatalog(division);
    });
  });
  retryButton.addEventListener("click", () => {
    catalogCache.delete(divisionInput.value);
    loadCatalog(divisionInput.value);
  });

  form.addEventListener("submit", (event) => {
    if (submitting) {
      event.preventDefault();
      return;
    }
    if (!selectedTeam || !seasonTeamInput.value) {
      event.preventDefault();
      combobox.setAttribute("aria-invalid", "true");
      catalogStatus.textContent = "Choose an NCAA team from the list.";
      combobox.focus();
      return;
    }
    submitting = true;
    submitButton.disabled = true;
    backLink.setAttribute("aria-disabled", "true");
    backLink.setAttribute("tabindex", "-1");
    backLink.addEventListener("click", (clickEvent) => clickEvent.preventDefault());
    submitStatus.textContent = "Accepting report request…";
  });

  const errorSummary = document.getElementById("form-errors");
  if (errorSummary) errorSummary.focus();
  loadCatalog(divisionInput.value);
})();
