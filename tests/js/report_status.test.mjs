import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);

function loadModule() {
  return require("../../static/report_status.js");
}

function fixture({ hidden = false } = {}) {
  let now = 0;
  let isHidden = hidden;
  const scheduled = [];
  const rendered = [];
  const announced = [];
  const responses = [];
  const fetchStatus = async () => {
    const response = responses.shift();
    if (response instanceof Error) throw response;
    return response;
  };
  const poller = loadModule().createReportStatusPoller({
    fetchStatus,
    now: () => now,
    schedule(callback, delay) {
      scheduled.push({ callback, delay });
      return scheduled.length;
    },
    cancel() {},
    isHidden: () => isHidden,
    render: (view) => rendered.push(view),
    announce: (copy) => announced.push(copy),
  });
  return {
    poller,
    responses,
    scheduled,
    rendered,
    announced,
    setNow(value) { now = value; },
    setHidden(value) { isHidden = value; },
  };
}

const queued = {
  state: "queued",
  heading: "Queued",
  message: "Waiting to collect live NCAA data.",
  updated_at: "2026-11-12T00:00:00+00:00",
  terminal: false,
  sections: [],
};

test("cadence progresses from three to five to ten seconds", async () => {
  const fx = fixture();
  fx.responses.push(queued, queued, queued);

  await fx.poller.start();
  assert.equal(fx.scheduled.pop().delay, 3000);
  fx.setNow(31_000);
  await fx.poller.checkNow();
  assert.equal(fx.scheduled.pop().delay, 5000);
  fx.setNow(121_000);
  await fx.poller.checkNow();
  assert.equal(fx.scheduled.pop().delay, 10_000);
});

test("hidden tabs pause and one visible transition polls immediately", async () => {
  const fx = fixture({ hidden: true });
  fx.responses.push(queued);

  await fx.poller.start();
  assert.equal(fx.rendered.length, 0);
  assert.equal(fx.scheduled.length, 0);
  fx.setHidden(false);
  await fx.poller.visibilityChanged();
  assert.equal(fx.rendered.length, 1);
  assert.equal(fx.scheduled.length, 1);
});

test("ready partial and failed are terminal and stop scheduling", async () => {
  for (const state of ["ready", "partial", "failed"]) {
    const fx = fixture();
    fx.responses.push({ ...queued, state, heading: state, terminal: true });
    await fx.poller.start();
    assert.equal(fx.rendered.at(-1).state, state);
    assert.equal(fx.scheduled.length, 0);
    assert.equal(fx.poller.isStopped(), true);
  }
});

test("network failures preserve durable view and expose fixed note after two", async () => {
  const fx = fixture();
  fx.responses.push(new TypeError("private network detail"), new Error("secret"));

  await fx.poller.start();
  assert.equal(fx.rendered.length, 0);
  assert.equal(fx.scheduled.pop().delay, 3000);
  await fx.poller.checkNow();
  assert.deepEqual(fx.rendered.at(-1), {
    transient_error: "Status temporarily unavailable",
  });
  assert.equal(JSON.stringify(fx.rendered).includes("secret"), false);
});

test("fifteen-minute timeout stops and manual check can resume", async () => {
  const fx = fixture();
  fx.responses.push(queued, queued);
  await fx.poller.start();
  fx.setNow(15 * 60 * 1000);
  await fx.poller.checkNow();
  assert.deepEqual(fx.rendered.at(-1), {
    state: "still_working",
    heading: "Still working",
    message: "Automatic updates paused. Check status to continue.",
    terminal: true,
    sections: [],
  });
  assert.equal(fx.scheduled.length, 1);

  await fx.poller.manualCheck();
  assert.equal(fx.rendered.at(-1).state, "queued");
});

test("announcements occur on state transitions only", async () => {
  const fx = fixture();
  fx.responses.push(queued, queued, {
    ...queued,
    state: "collecting",
    heading: "Collecting",
  });
  await fx.poller.start();
  await fx.poller.checkNow();
  await fx.poller.checkNow();
  assert.deepEqual(fx.announced, [
    "Refresh status changed to Queued.",
    "Refresh status changed to Collecting.",
  ]);
});

test("module is a safe no-op when progressive enhancement target is absent", () => {
  assert.equal(typeof loadModule().enhanceReportStatus, "function");
  assert.equal(loadModule().enhanceReportStatus(null), null);
});
