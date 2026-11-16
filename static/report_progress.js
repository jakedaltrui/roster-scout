(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.RosterScoutReportProgress = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const STORAGE_KEY = "roster-scout-run-times";
  const MIN_EXPECTED_MS = 10_000;
  const MAX_EXPECTED_MS = 180_000;

  function boundedExpectedDuration(value) {
    const duration = Number(value);
    if (!Number.isFinite(duration)) return 45_000;
    return Math.max(MIN_EXPECTED_MS, Math.min(MAX_EXPECTED_MS, duration));
  }

  function estimatedPercent(elapsedMs, expectedMs, state) {
    const elapsed = Math.max(0, Number(elapsedMs) || 0);
    const expected = boundedExpectedDuration(expectedMs);
    if (state === "ready" || state === "partial") return 100;
    if (state === "failed") return 100;
    if (state === "queued") {
      return Math.min(14, 8 + 6 * (1 - Math.exp(-elapsed / 5_000)));
    }

    const ratio = elapsed / expected;
    let collecting;
    if (ratio <= 1) {
      collecting = 14 + 74 * (1 - Math.pow(1 - ratio, 1.35));
    } else {
      collecting = 88 + 4 * (1 - Math.exp(-(ratio - 1) * 1.4));
    }
    if (state === "saving") {
      return Math.max(94, Math.min(97, collecting + 4));
    }
    if (state === "still_working") return Math.max(92, collecting);
    return Math.min(92, collecting);
  }

  function readExpectedDuration(storage) {
    try {
      const saved = JSON.parse(storage?.getItem(STORAGE_KEY) || "[]");
      const durations = Array.isArray(saved)
        ? saved
            .filter((value) => Number.isFinite(value)
              && value >= MIN_EXPECTED_MS && value <= MAX_EXPECTED_MS)
            .sort((left, right) => left - right)
        : [];
      return durations.length
        ? durations[Math.floor(durations.length / 2)]
        : 45_000;
    } catch (_error) {
      return 45_000;
    }
  }

  function recordDuration(storage, durationMs) {
    const duration = Number(durationMs);
    if (!storage || !Number.isFinite(duration)
      || duration < MIN_EXPECTED_MS || duration > MAX_EXPECTED_MS) return false;
    try {
      const saved = JSON.parse(storage.getItem(STORAGE_KEY) || "[]");
      const durations = Array.isArray(saved) ? saved : [];
      storage.setItem(STORAGE_KEY, JSON.stringify([...durations, duration].slice(-5)));
      return true;
    } catch (_error) {
      return false;
    }
  }

  function createProgressController(options = {}) {
    const now = options.now || Date.now;
    const schedule = options.schedule || setTimeout;
    const cancel = options.cancel || clearTimeout;
    const onUpdate = options.onUpdate || (() => {});
    const expectedMs = boundedExpectedDuration(options.expectedMs);
    const intervalMs = Math.max(100, Number(options.intervalMs) || 500);
    let state = "queued";
    let startedAt = null;
    let timer = null;
    let current = 8;
    let stopped = false;

    function emit(next) {
      current = Math.max(current, Math.min(100, Math.round(next)));
      onUpdate(current, state);
      return current;
    }

    function clearTimer() {
      if (timer !== null) cancel(timer);
      timer = null;
    }

    function queue() {
      clearTimer();
      if (stopped) return;
      timer = schedule(tick, intervalMs);
    }

    function tick() {
      if (startedAt === null) startedAt = now();
      emit(estimatedPercent(now() - startedAt, expectedMs, state));
      queue();
      return current;
    }

    function start(initialState = "queued") {
      clearTimer();
      stopped = false;
      state = initialState;
      startedAt = now();
      current = 8;
      tick();
      return current;
    }

    function sync(nextState) {
      if (typeof nextState === "string" && nextState) state = nextState;
      if (state === "collecting") emit(14);
      if (state === "saving") emit(94);
      return tick();
    }

    function complete(finalState = "ready") {
      state = finalState;
      stopped = true;
      clearTimer();
      return emit(100);
    }

    function stop() {
      stopped = true;
      clearTimer();
    }

    return {
      start,
      sync,
      complete,
      stop,
      value: () => current,
      state: () => state,
    };
  }

  return {
    STORAGE_KEY,
    createProgressController,
    estimatedPercent,
    readExpectedDuration,
    recordDuration,
  };
});
