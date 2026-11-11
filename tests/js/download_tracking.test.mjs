import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {
  createDownloadTracker,
  runDownloadSubmission,
} = require("../../static/download_tracking.js");

const FIRST_ID = "11111111-1111-4111-8111-111111111111";
const SECOND_ID = "22222222-2222-4222-8222-222222222222";
const PROOF = "signed-proof";
const START = 1_700_000_000_000;

class FakeStorage {
  constructor() {
    this.values = new Map();
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }

  removeItem(key) {
    this.values.delete(key);
  }
}

function trackerFixture() {
  const storage = new FakeStorage();
  let now = START;
  const ids = [FIRST_ID, SECOND_ID];
  const tracker = createDownloadTracker({
    storage,
    now: () => now,
    uuidFactory: () => ids.shift(),
  });
  return {
    storage,
    tracker,
    advance(milliseconds) {
      now += milliseconds;
    },
  };
}

test("ambiguous scrape and blob failures preserve one requesting UUID", () => {
  const { tracker } = trackerFixture();

  const first = tracker.beginOrReuse();
  assert.deepEqual(first, { downloadEventId: FIRST_ID, state: "requesting" });
  assert.equal(tracker.beginOrReuse().downloadEventId, FIRST_ID);
  assert.equal(tracker.pendingAcknowledgement(), null);
  assert.equal(tracker.beginOrReuse().downloadEventId, FIRST_ID);
});

test("network and blob exceptions remain ambiguous requesting attempts", async () => {
  for (const failurePoint of ["request", "blob"]) {
    const { tracker } = trackerFixture();
    const requestWorkbook = failurePoint === "request"
      ? async () => { throw new TypeError("lost scrape response"); }
      : async () => ({
        ok: true,
        headers: new Map([
          ["X-Roster-Scout-Download-Event-ID", FIRST_ID],
          ["X-Roster-Scout-Download-Ack-Proof", PROOF],
        ]),
        async blob() { throw new TypeError("lost blob response"); },
      });

    await assert.rejects(
      runDownloadSubmission({
        tracker,
        requestWorkbook,
        initiateDownload: async () => {},
        acknowledgeDownload: async () => ({ acknowledged: true }),
      }),
      TypeError,
    );
    assert.equal(tracker.beginOrReuse().downloadEventId, FIRST_ID);
  }
});

test("definitive HTTP failure abandons the UUID for the next submit", () => {
  const { tracker } = trackerFixture();

  assert.equal(tracker.beginOrReuse().downloadEventId, FIRST_ID);
  assert.equal(tracker.markDefinitiveFailure(FIRST_ID), true);
  assert.equal(tracker.markDefinitiveFailure(FIRST_ID), false);
  assert.equal(tracker.beginOrReuse().downloadEventId, SECOND_ID);
});

test("download initiation persists only opaque acknowledgement state", () => {
  const { storage, tracker } = trackerFixture();

  tracker.beginOrReuse();
  const pending = tracker.markDownloadInitiated(FIRST_ID, PROOF);
  assert.deepEqual(pending, {
    downloadEventId: FIRST_ID,
    acknowledgementProof: PROOF,
    state: "needs_ack",
  });
  assert.deepEqual(tracker.pendingAcknowledgement(), pending);
  const serialized = [...storage.values.values()].join("");
  assert.match(serialized, new RegExp(FIRST_ID));
  assert.match(serialized, /needs_ack/);
  assert.match(serialized, /signed-proof/);
  assert.doesNotMatch(serialized.toLowerCase(), /team|email|account|ip_address|user-agent|cookie|fingerprint/);
});

test("lost ack and duplicate callbacks keep idempotent lifecycle", () => {
  const { tracker } = trackerFixture();

  tracker.beginOrReuse();
  tracker.markDownloadInitiated(FIRST_ID, PROOF);
  assert.equal(tracker.pendingAcknowledgement().downloadEventId, FIRST_ID);
  assert.equal(tracker.pendingAcknowledgement().downloadEventId, FIRST_ID);

  assert.deepEqual(tracker.acknowledgeSuccess(FIRST_ID), {
    downloadEventId: FIRST_ID,
    state: "acknowledged",
  });
  assert.equal(tracker.acknowledgeSuccess(FIRST_ID), false);
  assert.equal(tracker.pendingAcknowledgement(), null);
  assert.equal(tracker.beginOrReuse().downloadEventId, SECOND_ID);
});

test("malformed and expired session state is discarded", () => {
  const { storage, tracker, advance } = trackerFixture();

  storage.setItem("roster-scout.download-attempt.v1", "not-json");
  assert.equal(tracker.beginOrReuse().downloadEventId, FIRST_ID);
  advance(15 * 60 * 1000 + 1);
  assert.equal(tracker.beginOrReuse().downloadEventId, SECOND_ID);
});

test("submission orders blob then download then ack and keeps needs_ack on ack failure", async () => {
  const { tracker } = trackerFixture();
  const calls = [];
  const response = {
    ok: true,
    headers: new Map([
      ["X-Roster-Scout-Download-Event-ID", FIRST_ID],
      ["X-Roster-Scout-Download-Ack-Proof", PROOF],
    ]),
    async blob() {
      calls.push("blob");
      return { workbook: true };
    },
  };

  const result = await runDownloadSubmission({
    tracker,
    requestWorkbook: async (downloadEventId) => {
      calls.push(`scrape:${downloadEventId}`);
      return response;
    },
    initiateDownload: async () => {
      calls.push("download");
    },
    acknowledgeDownload: async () => {
      calls.push("ack");
      throw new TypeError("lost acknowledgement response");
    },
  });

  assert.equal(result.downloadStarted, true);
  assert.equal(result.acknowledged, false);
  assert.deepEqual(calls, [`scrape:${FIRST_ID}`, "blob", "download", "ack"]);
  assert.equal(tracker.pendingAcknowledgement().downloadEventId, FIRST_ID);
});

test("pending ack retries before a fresh intentional submission", async () => {
  const { tracker } = trackerFixture();
  tracker.beginOrReuse();
  tracker.markDownloadInitiated(FIRST_ID, PROOF);
  const calls = [];

  const result = await runDownloadSubmission({
    tracker,
    retryPendingAcknowledgement: async (pending) => {
      calls.push(`retry:${pending.downloadEventId}`);
      return { acknowledged: true };
    },
    requestWorkbook: async (downloadEventId) => {
      calls.push(`scrape:${downloadEventId}`);
      return {
        ok: false,
        status: 422,
        headers: new Map(),
      };
    },
    initiateDownload: async () => calls.push("download"),
    acknowledgeDownload: async () => calls.push("ack"),
  });

  assert.equal(result.downloadStarted, false);
  assert.equal(result.definitiveFailure, true);
  assert.deepEqual(calls, [`retry:${FIRST_ID}`, `scrape:${SECOND_ID}`]);
  assert.equal(tracker.pendingAcknowledgement(), null);
});
