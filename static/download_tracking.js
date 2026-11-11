(function exposeDownloadTracking(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.RosterScoutDownloadTracking = api;
  }
})(typeof globalThis === "object" ? globalThis : this, function buildApi() {
  "use strict";

  const STORAGE_KEY = "roster-scout.download-attempt.v1";
  const PROOF_TTL_MS = 15 * 60 * 1000;
  const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

  function isUuid(value) {
    return typeof value === "string" && UUID_PATTERN.test(value);
  }

  function isTimestamp(value) {
    return Number.isSafeInteger(value) && value >= 0;
  }

  function isProof(value) {
    return typeof value === "string"
      && value.length > 0
      && value.length <= 512
      && !/[^\x20-\x7e]/.test(value);
  }

  function createDownloadTracker({ storage, uuidFactory, now = Date.now } = {}) {
    if (!storage || typeof storage.getItem !== "function"
        || typeof storage.setItem !== "function"
        || typeof storage.removeItem !== "function") {
      throw new TypeError("A session storage interface is required.");
    }
    if (typeof uuidFactory !== "function" || typeof now !== "function") {
      throw new TypeError("UUID and time providers are required.");
    }

    function discard() {
      storage.removeItem(STORAGE_KEY);
    }

    function save(record) {
      storage.setItem(STORAGE_KEY, JSON.stringify(record));
    }

    function load() {
      const serialized = storage.getItem(STORAGE_KEY);
      if (serialized === null) return null;

      let record;
      try {
        record = JSON.parse(serialized);
      } catch (_error) {
        discard();
        return null;
      }

      if (!record || typeof record !== "object" || Array.isArray(record)
          || !isUuid(record.download_event_id)
          || !isTimestamp(record.updated_at_ms)
          || now() - record.updated_at_ms > PROOF_TTL_MS
          || record.updated_at_ms > now() + PROOF_TTL_MS) {
        discard();
        return null;
      }

      if (record.state === "requesting") {
        const keys = Object.keys(record).sort();
        if (keys.join(",") !== "download_event_id,state,updated_at_ms") {
          discard();
          return null;
        }
        return record;
      }

      if (record.state === "needs_ack") {
        const keys = Object.keys(record).sort();
        if (keys.join(",") !== "acknowledgement_proof,download_event_id,server_receipt_at_ms,state,updated_at_ms"
            || !isProof(record.acknowledgement_proof)
            || !isTimestamp(record.server_receipt_at_ms)) {
          discard();
          return null;
        }
        return record;
      }

      discard();
      return null;
    }

    function publicRecord(record) {
      const result = {
        downloadEventId: record.download_event_id,
        state: record.state,
      };
      if (record.state === "needs_ack") {
        result.acknowledgementProof = record.acknowledgement_proof;
      }
      return result;
    }

    function beginOrReuse() {
      const current = load();
      if (current) return publicRecord(current);

      const downloadEventId = uuidFactory();
      if (!isUuid(downloadEventId)) {
        throw new TypeError("The UUID provider returned an invalid value.");
      }
      const record = {
        download_event_id: downloadEventId,
        state: "requesting",
        updated_at_ms: now(),
      };
      save(record);
      return publicRecord(record);
    }

    function markDefinitiveFailure(downloadEventId) {
      const current = load();
      if (!current || current.state !== "requesting"
          || current.download_event_id !== downloadEventId) {
        return false;
      }
      discard();
      return true;
    }

    function markDownloadInitiated(
      downloadEventId,
      acknowledgementProof,
      serverReceiptAtMs = now(),
    ) {
      const current = load();
      if (!current || current.state !== "requesting"
          || current.download_event_id !== downloadEventId
          || !isProof(acknowledgementProof)
          || !isTimestamp(serverReceiptAtMs)) {
        throw new TypeError("Download acknowledgement state is invalid.");
      }
      const record = {
        download_event_id: downloadEventId,
        state: "needs_ack",
        acknowledgement_proof: acknowledgementProof,
        server_receipt_at_ms: serverReceiptAtMs,
        updated_at_ms: now(),
      };
      save(record);
      return publicRecord(record);
    }

    function pendingAcknowledgement() {
      const current = load();
      return current && current.state === "needs_ack"
        ? publicRecord(current)
        : null;
    }

    function acknowledgeSuccess(downloadEventId) {
      const current = load();
      if (!current || current.state !== "needs_ack"
          || current.download_event_id !== downloadEventId) {
        return false;
      }
      discard();
      return true;
    }

    return {
      acknowledgeSuccess,
      beginOrReuse,
      markDefinitiveFailure,
      markDownloadInitiated,
      pendingAcknowledgement,
    };
  }

  async function runDownloadSubmission({
    tracker,
    retryPendingAcknowledgement,
    requestWorkbook,
    initiateDownload,
    acknowledgeDownload,
  }) {
    const pending = tracker.pendingAcknowledgement();
    if (pending) {
      if (typeof retryPendingAcknowledgement !== "function") {
        return { downloadStarted: false, pendingAcknowledgement: true };
      }
      try {
        const retryResult = await retryPendingAcknowledgement(pending);
        if (!retryResult || retryResult.acknowledged !== true) {
          return { downloadStarted: false, pendingAcknowledgement: true };
        }
        tracker.acknowledgeSuccess(pending.downloadEventId);
      } catch (_error) {
        return { downloadStarted: false, pendingAcknowledgement: true };
      }
    }

    const attempt = tracker.beginOrReuse();
    const response = await requestWorkbook(attempt.downloadEventId);
    if (!response.ok) {
      tracker.markDefinitiveFailure(attempt.downloadEventId);
      return {
        downloadStarted: false,
        definitiveFailure: true,
        response,
      };
    }

    const echoedId = response.headers.get("X-Roster-Scout-Download-Event-ID");
    const proof = response.headers.get("X-Roster-Scout-Download-Ack-Proof");
    if (echoedId !== attempt.downloadEventId || !isProof(proof)) {
      throw new TypeError("The workbook receipt is invalid.");
    }

    const workbook = await response.blob();
    await initiateDownload(workbook, response);
    const acknowledgement = tracker.markDownloadInitiated(
      attempt.downloadEventId,
      proof,
    );

    try {
      const acknowledgementResult = await acknowledgeDownload(acknowledgement);
      if (acknowledgementResult && acknowledgementResult.acknowledged === true) {
        tracker.acknowledgeSuccess(attempt.downloadEventId);
        return { downloadStarted: true, acknowledged: true, response };
      }
    } catch (_error) {
      // The workbook was already handed to the browser. Preserve needs_ack for retry.
    }
    return { downloadStarted: true, acknowledged: false, response };
  }

  return {
    PROOF_TTL_MS,
    STORAGE_KEY,
    createDownloadTracker,
    runDownloadSubmission,
  };
});
