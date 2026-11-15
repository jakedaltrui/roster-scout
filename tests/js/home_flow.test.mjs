import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {
  MAX_AGE_MS,
  STORAGE_KEY,
  createPendingReportStore,
  isResumeLocation,
  normalizeVerificationCode,
} = require("../../static/home_flow.js");

const SELECTION = {
  division: "d1",
  team: "Example University",
  season_team_id: "614845",
  team_id: "2",
  display_name: "Example University",
};

class Storage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}

test("pending selection is bounded, non-identifying, and consumed once", () => {
  const storage = new Storage();
  const store = createPendingReportStore({ storage, now: () => 1000 });
  assert.equal(store.save(SELECTION), true);
  const serialized = storage.getItem(STORAGE_KEY);
  assert.ok(serialized.length < 512);
  for (const forbidden of ["email", "account", "organization", "token", "return_to"])
    assert.equal(serialized.includes(forbidden), false);
  assert.deepEqual(store.consume(), SELECTION);
  assert.equal(store.consume(), null);
});

test("expired and tampered records are discarded", () => {
  const storage = new Storage();
  let now = 1000;
  const store = createPendingReportStore({ storage, now: () => now });
  store.save(SELECTION);
  now += MAX_AGE_MS + 1;
  assert.equal(store.load(), null);

  storage.setItem(STORAGE_KEY, JSON.stringify({ ...SELECTION, email: "coach@school.edu", saved_at_ms: now }));
  assert.equal(store.load(), null);
});

test("storage failure returns false and never weakens the sign-in boundary", () => {
  const storage = {
    getItem() { throw new Error("blocked"); },
    setItem() { throw new Error("blocked"); },
    removeItem() { throw new Error("blocked"); },
  };
  const store = createPendingReportStore({ storage, now: () => 1000 });
  assert.equal(store.save(SELECTION), false);
  assert.equal(store.consume(), null);
});

test("only the exact resume location is accepted", () => {
  assert.equal(isResumeLocation({ pathname: "/", search: "?resume=download" }), true);
  assert.equal(isResumeLocation({ pathname: "/", search: "?resume=download&next=https://evil.test" }), false);
  assert.equal(isResumeLocation({ pathname: "//evil.test", search: "?resume=download" }), false);
  assert.equal(isResumeLocation({ pathname: "/reports", search: "?resume=download" }), false);
});

test("verification codes accept full mobile paste and one-time-code autofill values", () => {
  assert.equal(normalizeVerificationCode("123456"), "123456");
  assert.equal(normalizeVerificationCode("123 456"), "123456");
  assert.equal(normalizeVerificationCode("Code: 987-654"), "987654");
  assert.equal(normalizeVerificationCode("123456789"), "123456");
  assert.equal(normalizeVerificationCode(null), "");
});
