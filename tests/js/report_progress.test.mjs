import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {
  createProgressController,
  estimatedPercent,
  readExpectedDuration,
  recordDuration,
} = require("../../static/report_progress.js");

class Storage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); }
}

test("estimated progress is monotonic and reserves 100 percent for completion", () => {
  const samples = [0, 5_000, 15_000, 30_000, 60_000, 180_000]
    .map((elapsed) => estimatedPercent(elapsed, 30_000, "collecting"));
  assert.deepEqual(samples, [...samples].sort((left, right) => left - right));
  assert.ok(samples.at(-1) < 100);
  assert.equal(estimatedPercent(1, 30_000, "ready"), 100);
});

test("queued work remains in the opening range and saving advances near completion", () => {
  assert.ok(estimatedPercent(120_000, 30_000, "queued") <= 14);
  assert.ok(estimatedPercent(15_000, 30_000, "saving") >= 94);
  assert.ok(estimatedPercent(120_000, 30_000, "saving") < 100);
});

test("controller follows server state and reaches 100 only on terminal completion", () => {
  let now = 0;
  const scheduled = [];
  const updates = [];
  const controller = createProgressController({
    now: () => now,
    expectedMs: 30_000,
    schedule(callback) { scheduled.push(callback); return scheduled.length; },
    cancel() {},
    onUpdate(value, state) { updates.push({ value, state }); },
  });
  controller.start("queued");
  now = 15_000;
  controller.sync("collecting");
  assert.ok(controller.value() > 14);
  assert.ok(controller.value() < 100);
  controller.complete("ready");
  assert.equal(controller.value(), 100);
  assert.equal(updates.at(-1).state, "ready");
});

test("recent successful durations use a bounded median", () => {
  const storage = new Storage();
  assert.equal(recordDuration(storage, 20_000), true);
  assert.equal(recordDuration(storage, 60_000), true);
  assert.equal(recordDuration(storage, 40_000), true);
  assert.equal(recordDuration(storage, 500), false);
  assert.equal(readExpectedDuration(storage), 40_000);
});
