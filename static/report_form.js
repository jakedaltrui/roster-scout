(() => {
  "use strict";

  const form = document.getElementById("report-create-form");
  if (!form) return;

  const divisionInput = document.getElementById("report-division");
  const seasonTeamInput = document.getElementById("season-team-id");
  const combobox = document.getElementById("team-combobox");
  const results = document.getElementById("team-results");
  const catalogStatus = document.getElementById("catalog-status");
  const retryButton = document.getElementById("catalog-retry");
  const submitButton = document.getElementById("create-report-submit");
  const backLink = document.getElementById("back-to-reports");
  const submitStatus = document.getElementById("submit-status");
  const divisionGroup = document.querySelector(".division-group");
  const loadingPanel = document.getElementById("report-loading");
  const loadingTeam = document.getElementById("report-loading-team");
  const loadingTitle = document.getElementById("report-loading-title");
  const loadingProgress = document.getElementById("report-loading-progress");
  const loadingViewport = document.getElementById("report-loading-viewport");
  const loadingMeter = document.getElementById("report-loading-meter");
  const loadingPercent = document.getElementById("report-loading-percent");
  const loadingSteps = Array.from(document.querySelectorAll("[data-loading-step]"));
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
  let loadingProgressController = null;
  let runStartedAt = null;
  let loadingHeadlineSwapTimer = null;
  let loadingHeadlineAnimation = null;

  const LOADING_STAGE_MOTION_MS = 820;
  const LOADING_HEADLINE_EXIT_MS = 180;

  const loadingHeadlines = [
    "Opponent locked in.",
    "Calling the bullpen for data.",
    "Rounding up the season numbers.",
    "Checking the lineup card twice.",
    "Getting every player into position.",
    "Giving the workbook its final polish.",
    "Packing up both export formats.",
    "Putting this report in the books.",
  ];

  function stopLoadingHeadlineTransition() {
    window.clearTimeout(loadingHeadlineSwapTimer);
    loadingHeadlineSwapTimer = null;
    loadingHeadlineAnimation?.cancel();
    loadingHeadlineAnimation = null;
  }

  function transitionLoadingHeadline(copy, immediate = false) {
    if (!loadingTitle) return;
    stopLoadingHeadlineTransition();
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (immediate || reducedMotion || typeof loadingTitle.animate !== "function") {
      loadingTitle.textContent = copy;
      return;
    }
    if (loadingTitle.textContent === copy) return;
    loadingHeadlineAnimation = loadingTitle.animate(
      [
        { opacity: 1, transform: "translateY(0)" },
        { opacity: 0, transform: "translateY(-6px)" },
      ],
      {
        duration: LOADING_HEADLINE_EXIT_MS,
        easing: "ease-in",
        fill: "forwards",
      }
    );
    loadingHeadlineSwapTimer = window.setTimeout(() => {
      loadingTitle.textContent = copy;
      loadingHeadlineAnimation = loadingTitle.animate(
        [
          { opacity: 0, transform: "translateY(6px)" },
          { opacity: 1, transform: "translateY(0)" },
        ],
        {
          duration: LOADING_STAGE_MOTION_MS - LOADING_HEADLINE_EXIT_MS,
          easing: "cubic-bezier(.22,1,.36,1)",
          fill: "both",
        }
      );
      loadingHeadlineSwapTimer = null;
    }, LOADING_HEADLINE_EXIT_MS);
  }

  function updateLoadingStage(index, { immediate = false } = {}) {
    const bounded = Math.max(0, Math.min(index, loadingSteps.length - 1));
    loadingSteps.forEach((step, position) => {
      step.classList.toggle("is-current", position === bounded);
      step.classList.toggle("is-complete", position < bounded);
      const status = step.querySelector("[data-loading-status]");
      if (status) {
        status.textContent = position < bounded
          ? " — complete"
          : position === bounded ? " — in progress" : "";
      }
    });
    transitionLoadingHeadline(loadingHeadlines[bounded], immediate);
    const active = loadingSteps[bounded];
    if (active && loadingProgress && loadingViewport) {
      loadingProgress.style.setProperty("--activity-top", `${active.offsetTop}px`);
      loadingProgress.style.setProperty("--activity-height", `${active.offsetHeight}px`);
      const top = Math.max(
        0,
        active.offsetTop - (loadingViewport.clientHeight - active.offsetHeight) / 2
      );
      loadingViewport.scrollTo({
        top,
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
      });
    }
  }

  function beginLoading(teamName) {
    form.classList.add("is-loading");
    loadingPanel?.setAttribute("aria-hidden", "false");
    if (loadingTeam) loadingTeam.textContent = teamName;
    if (loadingProgress && loadingViewport && loadingSteps.length) {
      const finalStep = loadingSteps[loadingSteps.length - 1];
      const padding = Math.max(
        8,
        (loadingViewport.clientHeight - finalStep.offsetHeight) / 2
      );
      loadingProgress.style.paddingBottom = `${padding}px`;
      loadingProgress.style.setProperty(
        "--connector-bottom",
        `${padding + finalStep.offsetHeight / 2}px`
      );
      loadingProgress.classList.add("is-running");
    }
    updateLoadingStage(1, { immediate: true });
    let storage = null;
    try { storage = window.localStorage; } catch (_error) { /* Progress remains available. */ }
    const progressApi = window.RosterScoutReportProgress;
    loadingProgressController = progressApi.createProgressController({
      expectedMs: progressApi.readExpectedDuration(storage),
      onUpdate(percent) {
        loadingMeter?.style.setProperty("--report-progress-scale", String(percent / 100));
        loadingMeter?.setAttribute("aria-valuenow", String(percent));
        if (loadingPercent) loadingPercent.textContent = `${percent}%`;
        const thresholds = [0, 22, 37, 52, 68, 82, 95];
        let stage = 1;
        thresholds.forEach((threshold, index) => {
          if (percent >= threshold) stage = index + 1;
        });
        updateLoadingStage(stage);
      },
    });
    loadingProgressController.start("queued");
  }

  function restoreForm(message) {
    loadingProgressController?.stop();
    loadingProgressController = null;
    stopLoadingHeadlineTransition();
    submitting = false;
    form.classList.remove("is-loading");
    loadingPanel?.setAttribute("aria-hidden", "true");
    loadingProgress?.classList.remove("is-running");
    submitButton.disabled = false;
    backLink.removeAttribute("aria-disabled");
    backLink.removeAttribute("tabindex");
    submitStatus.textContent = message;
    submitStatus.focus?.();
  }

  function updateDivisionThumb() {
    const selectedIndex = Math.max(0, divisionButtons.findIndex(
      (button) => button.getAttribute("aria-pressed") === "true"
    ));
    divisionGroup?.style.setProperty("--division-index", selectedIndex);
  }

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
    const logoUrl = String(team.logo_url || "").trim();
    if (logoUrl || /^\d+$/.test(teamId)) {
      const image = document.createElement("img");
      image.alt = "";
      image.loading = "lazy";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("load", () => { fallback.hidden = true; });
      image.addEventListener("error", () => { image.remove(); });
      image.src = logoUrl || `/api/team-logo/${encodeURIComponent(teamId)}`;
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
    catalogStatus.textContent = "";
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
      catalogStatus.textContent = "";
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
      updateDivisionThumb();
      loadCatalog(division);
    });
  });
  retryButton.addEventListener("click", () => {
    catalogCache.delete(divisionInput.value);
    loadCatalog(divisionInput.value);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting) {
      return;
    }
    if (!selectedTeam || !seasonTeamInput.value) {
      combobox.setAttribute("aria-invalid", "true");
      catalogStatus.textContent = "Choose an NCAA team from the list.";
      combobox.focus();
      return;
    }
    submitting = true;
    submitButton.disabled = true;
    backLink.setAttribute("aria-disabled", "true");
    backLink.setAttribute("tabindex", "-1");
    submitStatus.textContent = "";
    runStartedAt = performance.now();
    beginLoading(teamLabel(selectedTeam));

    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.report_url || !payload.status_url) {
        throw new Error(payload.error || "We couldn’t start that report. Try again.");
      }
      const statusApi = window.RosterScoutReportStatus;
      if (!statusApi?.createReportStatusPoller) {
        window.location.assign(payload.report_url);
        return;
      }
      const poller = statusApi.createReportStatusPoller({
        async fetchStatus() {
          const statusResponse = await fetch(payload.status_url, {
            credentials: "same-origin",
            headers: { Accept: "application/json" },
          });
          const status = await statusResponse.json();
          if (!statusResponse.ok || !statusApi.validStatusView(status)) {
            throw new Error("status unavailable");
          }
          return status;
        },
        render(view) {
          loadingProgressController?.sync(view.state);
          if (view.transient_error && loadingTitle) {
            transitionLoadingHeadline("Still tracking down the numbers.");
          }
        },
        announce(copy) {
          if (loadingPanel) loadingPanel.setAttribute("aria-label", copy);
        },
        terminalReached(view) {
          let storage = null;
          try { storage = window.localStorage; } catch (_error) { /* Duration history is optional. */ }
          if (runStartedAt !== null) {
            window.RosterScoutReportProgress.recordDuration(
              storage,
              performance.now() - runStartedAt
            );
          }
          loadingProgressController?.complete(view.state);
          if (view.state === "failed") {
            transitionLoadingHeadline("That run needs another look.");
            window.setTimeout(
              () => window.location.assign(payload.report_url),
              LOADING_STAGE_MOTION_MS
            );
            return;
          }
          window.setTimeout(
            () => window.location.assign(payload.report_url),
            LOADING_STAGE_MOTION_MS
          );
        },
      });
      document.addEventListener("visibilitychange", () => {
        void poller.visibilityChanged();
      });
      await poller.start();
    } catch (error) {
      restoreForm(error.message || "We couldn’t start that report. Try again.");
    }
  });

  const errorSummary = document.getElementById("form-errors");
  if (errorSummary) errorSummary.focus();
  updateDivisionThumb();
  loadCatalog(divisionInput.value);
})();
