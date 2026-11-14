(function exposeHomeFlow(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.RosterScoutHomeFlow = api;
  }
})(typeof globalThis === "object" ? globalThis : this, function buildApi() {
  "use strict";

  const STORAGE_KEY = "roster-scout.pending-report.v1";
  const MAX_AGE_MS = 15 * 60 * 1000;
  const ALLOWED_DIVISIONS = new Set(["d1", "d2", "d3"]);

  function validText(value, maximum) {
    return typeof value === "string"
      && value.length > 0
      && value.length <= maximum
      && !/[\u0000-\u001f\u007f]/.test(value);
  }

  function canonicalSelection(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const keys = Object.keys(value).sort().join(",");
    if (keys !== "display_name,division,season_team_id,team,team_id") return null;
    if (!ALLOWED_DIVISIONS.has(value.division)
        || !validText(value.team, 160)
        || !validText(value.display_name, 160)
        || !validText(value.season_team_id, 12)
        || !/^\d{1,12}$/.test(value.season_team_id)
        || !validText(value.team_id, 12)
        || !/^\d{1,12}$/.test(value.team_id)) {
      return null;
    }
    return {
      division: value.division,
      team: value.team,
      season_team_id: value.season_team_id,
      team_id: value.team_id,
      display_name: value.display_name,
    };
  }

  function createPendingReportStore({ storage, now = Date.now } = {}) {
    if (typeof now !== "function") throw new TypeError("A time provider is required.");

    function discard() {
      try {
        storage.removeItem(STORAGE_KEY);
      } catch (_error) {
        return false;
      }
      return true;
    }

    function save(selection) {
      const canonical = canonicalSelection(selection);
      if (!canonical) return false;
      const record = { ...canonical, saved_at_ms: now() };
      try {
        const serialized = JSON.stringify(record);
        if (serialized.length > 512) return false;
        storage.setItem(STORAGE_KEY, serialized);
        return storage.getItem(STORAGE_KEY) === serialized;
      } catch (_error) {
        return false;
      }
    }

    function load() {
      let serialized;
      try {
        serialized = storage.getItem(STORAGE_KEY);
      } catch (_error) {
        return null;
      }
      if (serialized === null || serialized.length > 512) return null;
      let record;
      try {
        record = JSON.parse(serialized);
      } catch (_error) {
        discard();
        return null;
      }
      if (!record || typeof record !== "object" || Array.isArray(record)) {
        discard();
        return null;
      }
      const { saved_at_ms: savedAt, ...selection } = record;
      const canonical = canonicalSelection(selection);
      if (!canonical
          || !Number.isSafeInteger(savedAt)
          || savedAt < 0
          || now() - savedAt > MAX_AGE_MS
          || savedAt > now() + MAX_AGE_MS) {
        discard();
        return null;
      }
      return canonical;
    }

    function consume() {
      const selection = load();
      discard();
      return selection;
    }

    return { save, load, consume, discard };
  }

  function isResumeLocation(location) {
    return Boolean(location)
      && location.pathname === "/"
      && location.search === "?resume=download";
  }

  return {
    MAX_AGE_MS,
    STORAGE_KEY,
    canonicalSelection,
    createPendingReportStore,
    isResumeLocation,
  };
});
