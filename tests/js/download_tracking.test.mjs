import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const {
  classifyAcknowledgementResponse,
  createSafeStorage,
  createDownloadTracker,
  runAuthenticatedSnapshotDownload,
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

class FailingStorage extends FakeStorage {
  constructor() {
    super();
    this.failGet = false;
    this.failSet = false;
    this.failRemove = false;
    this.calls = [];
  }

  getItem(key) {
    this.calls.push(`get:${key}`);
    if (this.failGet) throw new DOMException("storage blocked", "SecurityError");
    return super.getItem(key);
  }

  setItem(key, value) {
    this.calls.push(`set:${key}`);
    if (this.failSet) throw new DOMException("quota exceeded", "QuotaExceededError");
    super.setItem(key, value);
  }

  removeItem(key) {
    this.calls.push(`remove:${key}`);
    if (this.failRemove) throw new DOMException("storage blocked", "SecurityError");
    super.removeItem(key);
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

test("safe storage falls back permanently when a storage method throws", () => {
  const candidate = new FailingStorage();
  const storage = createSafeStorage(candidate);

  storage.setItem("attempt", "pending");
  candidate.failGet = true;
  assert.equal(storage.getItem("attempt"), "pending");

  candidate.failGet = false;
  storage.setItem("later", "memory-only");
  assert.equal(storage.getItem("later"), "memory-only");
  assert.equal(candidate.values.has("later"), false);
});

test("tracker remains usable when storage acquisition supplied no candidate", () => {
  const ids = [FIRST_ID];
  const tracker = createDownloadTracker({
    storage: null,
    now: () => START,
    uuidFactory: () => ids.shift(),
  });

  assert.deepEqual(tracker.beginOrReuse(), {
    downloadEventId: FIRST_ID,
    state: "requesting",
  });
});

test("successful persistent write survives a later read failure without duplicate workbook", async () => {
  const candidate = new FailingStorage();
  const ids = [FIRST_ID, SECOND_ID];
  const tracker = createDownloadTracker({
    storage: candidate,
    now: () => START,
    uuidFactory: () => ids.shift(),
  });
  tracker.beginOrReuse();
  tracker.markDownloadInitiated(FIRST_ID, PROOF);
  candidate.failGet = true;
  let workbookRequests = 0;

  const result = await runDownloadSubmission({
    tracker,
    retryPendingAcknowledgement: async (pending) => {
      assert.equal(pending.downloadEventId, FIRST_ID);
      return { acknowledged: false };
    },
    requestWorkbook: async () => {
      workbookRequests += 1;
      throw new Error("workbook request must not run");
    },
    initiateDownload: async () => {},
    acknowledgeDownload: async () => ({ outcome: "acknowledged" }),
  });

  assert.deepEqual(result, {
    downloadStarted: false,
    pendingAcknowledgement: true,
  });
  assert.equal(workbookRequests, 0);
  assert.equal(tracker.pendingAcknowledgement().downloadEventId, FIRST_ID);
});

test("set and remove failures preserve logical state in page memory", () => {
  const candidate = new FailingStorage();
  candidate.failSet = true;
  const ids = [FIRST_ID, SECOND_ID];
  const tracker = createDownloadTracker({
    storage: candidate,
    now: () => START,
    uuidFactory: () => ids.shift(),
  });

  assert.equal(tracker.beginOrReuse().downloadEventId, FIRST_ID);
  candidate.failSet = false;
  candidate.failRemove = true;
  assert.equal(tracker.markDefinitiveFailure(FIRST_ID), true);
  assert.equal(tracker.beginOrReuse().downloadEventId, SECOND_ID);
});

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
        acknowledgeDownload: async () => ({ outcome: "acknowledged" }),
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
      return { outcome: "acknowledged" };
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

test("acknowledgement HTTP statuses classify definitive and retryable outcomes", () => {
  for (const status of [200, 201, 204]) {
    assert.deepEqual(classifyAcknowledgementResponse(status, true), {
      outcome: "acknowledged",
    });
    assert.deepEqual(classifyAcknowledgementResponse(status, false), {
      outcome: "retryable",
    });
  }
  for (const status of [400, 401, 403, 404, 409, 422]) {
    assert.deepEqual(classifyAcknowledgementResponse(status, false), {
      outcome: "definitive",
    });
  }
  for (const status of [408, 425, 429, 500, 502, 503]) {
    assert.deepEqual(classifyAcknowledgementResponse(status, false), {
      outcome: "retryable",
    });
  }
});

test("definitive pending acknowledgement ends this submit and next submit uses a fresh UUID", async () => {
  const { tracker } = trackerFixture();
  tracker.beginOrReuse();
  tracker.markDownloadInitiated(FIRST_ID, PROOF);
  const calls = [];

  const rejected = await runDownloadSubmission({
    tracker,
    retryPendingAcknowledgement: async (pending) => {
      calls.push(`ack:${pending.downloadEventId}`);
      return { outcome: "definitive" };
    },
    requestWorkbook: async (downloadEventId) => {
      calls.push(`scrape:${downloadEventId}`);
      throw new Error("must not prepare a workbook after definitive rejection");
    },
    initiateDownload: async () => {},
    acknowledgeDownload: async () => ({ outcome: "acknowledged" }),
  });

  assert.deepEqual(rejected, {
    downloadStarted: false,
    definitiveAcknowledgementFailure: true,
  });
  assert.deepEqual(calls, [`ack:${FIRST_ID}`]);
  assert.equal(tracker.pendingAcknowledgement(), null);

  const later = await runDownloadSubmission({
    tracker,
    requestWorkbook: async (downloadEventId) => {
      calls.push(`scrape:${downloadEventId}`);
      return { ok: false, status: 422, headers: new Map() };
    },
    initiateDownload: async () => {},
    acknowledgeDownload: async () => ({ outcome: "acknowledged" }),
  });
  assert.equal(later.definitiveFailure, true);
  assert.deepEqual(calls, [`ack:${FIRST_ID}`, `scrape:${SECOND_ID}`]);
});

test("retryable acknowledgement outcomes and network errors preserve one pending UUID", async () => {
  for (const retry of [
    async () => ({ outcome: "retryable" }),
    async () => { throw new TypeError("network lost"); },
  ]) {
    const { tracker } = trackerFixture();
    tracker.beginOrReuse();
    tracker.markDownloadInitiated(FIRST_ID, PROOF);
    let workbookRequests = 0;

    const result = await runDownloadSubmission({
      tracker,
      retryPendingAcknowledgement: retry,
      requestWorkbook: async () => {
        workbookRequests += 1;
        throw new Error("must remain acknowledgement-only");
      },
      initiateDownload: async () => {},
      acknowledgeDownload: async () => ({ outcome: "acknowledged" }),
    });

    assert.deepEqual(result, {
      downloadStarted: false,
      pendingAcknowledgement: true,
    });
    assert.equal(workbookRequests, 0);
    assert.equal(tracker.pendingAcknowledgement().downloadEventId, FIRST_ID);
  }
});

test("definitive acknowledgement after download clears pending without undoing started download", async () => {
  const { tracker } = trackerFixture();
  const response = {
    ok: true,
    headers: new Map([
      ["X-Roster-Scout-Download-Event-ID", FIRST_ID],
      ["X-Roster-Scout-Download-Ack-Proof", PROOF],
    ]),
    async blob() { return { workbook: true }; },
  };

  const result = await runDownloadSubmission({
    tracker,
    requestWorkbook: async () => response,
    initiateDownload: async () => {},
    acknowledgeDownload: async () => ({ outcome: "definitive" }),
  });

  assert.equal(result.downloadStarted, true);
  assert.equal(result.acknowledged, false);
  assert.equal(result.definitiveAcknowledgementFailure, true);
  assert.equal(tracker.pendingAcknowledgement(), null);
  assert.equal(tracker.beginOrReuse().downloadEventId, SECOND_ID);
});

test("authenticated snapshot preparation reads blob then initiates anchor then acknowledges", async () => {
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
      return { exactStoredArtifact: true };
    },
  };

  const result = await runAuthenticatedSnapshotDownload({
    tracker,
    prepareSnapshot: async () => {
      calls.push("prepare");
      return response;
    },
    initiateDownload: async (blob) => {
      assert.equal(blob.exactStoredArtifact, true);
      calls.push("anchor");
    },
    acknowledgeDownload: async (pending) => {
      assert.equal(pending.downloadEventId, FIRST_ID);
      assert.equal(pending.acknowledgementProof, PROOF);
      calls.push("ack");
      return { outcome: "acknowledged" };
    },
  });

  assert.deepEqual(calls, ["prepare", "blob", "anchor", "ack"]);
  assert.equal(result.downloadStarted, true);
  assert.equal(result.acknowledged, true);
  assert.equal(tracker.pendingAcknowledgement(), null);
});

test("authenticated rejected missing-proof and foreign preparations never initiate or acknowledge", async () => {
  for (const [label, response] of [
    ["rejected", { ok: false, status: 403, headers: new Map() }],
    ["foreign", { ok: false, status: 404, headers: new Map() }],
    ["unprepared", { ok: false, status: 409, headers: new Map() }],
  ]) {
    const { tracker } = trackerFixture();
    let anchors = 0;
    let acknowledgements = 0;
    const result = await runAuthenticatedSnapshotDownload({
      tracker,
      prepareSnapshot: async () => response,
      initiateDownload: async () => { anchors += 1; },
      acknowledgeDownload: async () => {
        acknowledgements += 1;
        return { outcome: "acknowledged" };
      },
    });
    assert.equal(result.definitiveFailure, true, label);
    assert.equal(anchors, 0, label);
    assert.equal(acknowledgements, 0, label);
  }

  const { tracker } = trackerFixture();
  await assert.rejects(
    runAuthenticatedSnapshotDownload({
      tracker,
      prepareSnapshot: async () => ({
        ok: true,
        headers: new Map([["X-Roster-Scout-Download-Event-ID", FIRST_ID]]),
        async blob() { throw new Error("must not read without proof"); },
      }),
      initiateDownload: async () => { throw new Error("must not initiate"); },
      acknowledgeDownload: async () => { throw new Error("must not acknowledge"); },
    }),
    /receipt is invalid/,
  );
});

test("authenticated ambiguous acknowledgement survives reload and retries ack only", async () => {
  const storage = new FakeStorage();
  const firstTracker = createDownloadTracker({
    storage,
    now: () => START,
    uuidFactory: () => FIRST_ID,
  });
  let blobReads = 0;
  let anchors = 0;
  const first = await runAuthenticatedSnapshotDownload({
    tracker: firstTracker,
    prepareSnapshot: async () => ({
      ok: true,
      headers: new Map([
        ["X-Roster-Scout-Download-Event-ID", FIRST_ID],
        ["X-Roster-Scout-Download-Ack-Proof", PROOF],
      ]),
      async blob() { blobReads += 1; return { workbook: true }; },
    }),
    initiateDownload: async () => { anchors += 1; },
    acknowledgeDownload: async () => { throw new TypeError("response lost"); },
  });
  assert.equal(first.downloadStarted, true);
  assert.equal(first.acknowledged, false);

  const reloadedTracker = createDownloadTracker({
    storage,
    now: () => START,
    uuidFactory: () => SECOND_ID,
  });
  let preparations = 0;
  const retry = await runAuthenticatedSnapshotDownload({
    tracker: reloadedTracker,
    retryPendingAcknowledgement: async (pending) => {
      assert.equal(pending.downloadEventId, FIRST_ID);
      assert.equal(pending.acknowledgementProof, PROOF);
      return { outcome: "retryable" };
    },
    prepareSnapshot: async () => { preparations += 1; },
    initiateDownload: async () => { anchors += 1; },
    acknowledgeDownload: async () => ({ outcome: "acknowledged" }),
  });

  assert.deepEqual(retry, {
    downloadStarted: false,
    pendingAcknowledgement: true,
  });
  assert.equal(preparations, 0);
  assert.equal(blobReads, 1);
  assert.equal(anchors, 1);
  assert.equal(reloadedTracker.pendingAcknowledgement().downloadEventId, FIRST_ID);
});
