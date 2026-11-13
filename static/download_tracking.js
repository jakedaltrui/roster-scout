(function exposeDownloadTracking(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.RosterScoutDownloadTracking = api;
    if (root.document) {
      root.document.addEventListener("DOMContentLoaded", () => {
        api.attachAuthenticatedSnapshotDownloads({
          document: root.document,
          window: root,
        });
      });
    }
  }
})(typeof globalThis === "object" ? globalThis : this, function buildApi() {
  "use strict";

  const STORAGE_KEY = "roster-scout.download-attempt.v1";
  const AUTHENTICATED_STORAGE_KEY = "roster-scout.authenticated-download-attempt.v1";
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

  function createSafeStorage(storageCandidate) {
    const shadow = new Map();
    let useShadowOnly = false;

    function shadowGet(key) {
      return shadow.has(key) ? shadow.get(key) : null;
    }

    return {
      getItem(key) {
        const normalizedKey = String(key);
        if (useShadowOnly) return shadowGet(normalizedKey);
        try {
          const method = storageCandidate && storageCandidate.getItem;
          if (typeof method !== "function") throw new TypeError("Storage getItem is unavailable.");
          const value = method.call(storageCandidate, normalizedKey);
          if (value === null) {
            shadow.delete(normalizedKey);
            return null;
          }
          const normalizedValue = String(value);
          shadow.set(normalizedKey, normalizedValue);
          return normalizedValue;
        } catch (_error) {
          useShadowOnly = true;
          return shadowGet(normalizedKey);
        }
      },

      setItem(key, value) {
        const normalizedKey = String(key);
        const normalizedValue = String(value);
        if (!useShadowOnly) {
          try {
            const method = storageCandidate && storageCandidate.setItem;
            if (typeof method !== "function") throw new TypeError("Storage setItem is unavailable.");
            method.call(storageCandidate, normalizedKey, normalizedValue);
            shadow.set(normalizedKey, normalizedValue);
            return;
          } catch (_error) {
            useShadowOnly = true;
          }
        }
        shadow.set(normalizedKey, normalizedValue);
      },

      removeItem(key) {
        const normalizedKey = String(key);
        if (!useShadowOnly) {
          try {
            const method = storageCandidate && storageCandidate.removeItem;
            if (typeof method !== "function") throw new TypeError("Storage removeItem is unavailable.");
            method.call(storageCandidate, normalizedKey);
            shadow.delete(normalizedKey);
            return;
          } catch (_error) {
            useShadowOnly = true;
          }
        }
        shadow.delete(normalizedKey);
      },
    };
  }

  function classifyAcknowledgementResponse(status, acknowledged) {
    const normalizedStatus = Number(status);
    if (normalizedStatus >= 200 && normalizedStatus < 300) {
      return {
        outcome: acknowledged === true ? "acknowledged" : "retryable",
      };
    }
    if (normalizedStatus >= 400 && normalizedStatus < 500
        && ![408, 425, 429].includes(normalizedStatus)) {
      return { outcome: "definitive" };
    }
    return { outcome: "retryable" };
  }

  function createDownloadTracker({
    storage,
    uuidFactory,
    now = Date.now,
    storageKey = STORAGE_KEY,
  } = {}) {
    if (typeof uuidFactory !== "function" || typeof now !== "function") {
      throw new TypeError("UUID and time providers are required.");
    }
    if (typeof storageKey !== "string" || storageKey.length === 0) {
      throw new TypeError("A storage key is required.");
    }
    const safeStorage = createSafeStorage(storage);

    function discard() {
      safeStorage.removeItem(storageKey);
    }

    function save(record) {
      safeStorage.setItem(storageKey, JSON.stringify(record));
    }

    function load() {
      const serialized = safeStorage.getItem(storageKey);
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

    function markPreparedDownloadInitiated(
      downloadEventId,
      acknowledgementProof,
      serverReceiptAtMs = now(),
    ) {
      if (
        !isUuid(downloadEventId)
        || !isProof(acknowledgementProof)
        || !isTimestamp(serverReceiptAtMs)
      ) {
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
      return {
        downloadEventId,
        state: "acknowledged",
      };
    }

    function abandonPendingAcknowledgement(downloadEventId) {
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
      abandonPendingAcknowledgement,
      beginOrReuse,
      markDefinitiveFailure,
      markDownloadInitiated,
      markPreparedDownloadInitiated,
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
        if (retryResult && retryResult.outcome === "acknowledged") {
          tracker.acknowledgeSuccess(pending.downloadEventId);
        } else if (retryResult && retryResult.outcome === "definitive") {
          tracker.abandonPendingAcknowledgement(pending.downloadEventId);
          return {
            downloadStarted: false,
            definitiveAcknowledgementFailure: true,
          };
        } else {
          return { downloadStarted: false, pendingAcknowledgement: true };
        }
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
      if (acknowledgementResult && acknowledgementResult.outcome === "acknowledged") {
        tracker.acknowledgeSuccess(attempt.downloadEventId);
        return { downloadStarted: true, acknowledged: true, response };
      }
      if (acknowledgementResult && acknowledgementResult.outcome === "definitive") {
        tracker.abandonPendingAcknowledgement(attempt.downloadEventId);
        return {
          downloadStarted: true,
          acknowledged: false,
          definitiveAcknowledgementFailure: true,
          response,
        };
      }
    } catch (_error) {
      // The workbook was already handed to the browser. Preserve needs_ack for retry.
    }
    return { downloadStarted: true, acknowledged: false, response };
  }

  async function retryPendingDownloadAcknowledgement({
    tracker,
    acknowledgeDownload,
  }) {
    const pending = tracker.pendingAcknowledgement();
    if (!pending) return { pendingAcknowledgement: false };
    try {
      const result = await acknowledgeDownload(pending);
      if (result && result.outcome === "acknowledged") {
        tracker.acknowledgeSuccess(pending.downloadEventId);
        return { pendingAcknowledgement: false, acknowledged: true };
      }
      if (result && result.outcome === "definitive") {
        tracker.abandonPendingAcknowledgement(pending.downloadEventId);
        return {
          pendingAcknowledgement: false,
          definitiveAcknowledgementFailure: true,
        };
      }
    } catch (_error) {
      // Preserve the receipt and proof for another acknowledgement-only retry.
    }
    return { pendingAcknowledgement: true };
  }

  async function runAuthenticatedSnapshotDownload({
    tracker,
    retryPendingAcknowledgement,
    prepareSnapshot,
    initiateDownload,
    acknowledgeDownload,
  }) {
    if (typeof prepareSnapshot !== "function") {
      throw new TypeError("An authenticated snapshot preparation function is required.");
    }
    const pending = tracker.pendingAcknowledgement();
    if (pending) {
      if (typeof retryPendingAcknowledgement !== "function") {
        return { downloadStarted: false, pendingAcknowledgement: true };
      }
      try {
        const retryResult = await retryPendingAcknowledgement(pending);
        if (retryResult && retryResult.outcome === "acknowledged") {
          tracker.acknowledgeSuccess(pending.downloadEventId);
          return { downloadStarted: false, acknowledgementRetried: true };
        }
        if (retryResult && retryResult.outcome === "definitive") {
          tracker.abandonPendingAcknowledgement(pending.downloadEventId);
          return {
            downloadStarted: false,
            definitiveAcknowledgementFailure: true,
          };
        }
      } catch (_error) {
        // Keep the exact receipt/proof and never prepare or initiate again.
      }
      return { downloadStarted: false, pendingAcknowledgement: true };
    }

    const response = await prepareSnapshot();
    if (!response.ok) {
      return { downloadStarted: false, definitiveFailure: true, response };
    }
    const receipt = response.headers.get("X-Roster-Scout-Download-Event-ID");
    const proof = response.headers.get("X-Roster-Scout-Download-Ack-Proof");
    if (!isUuid(receipt) || !isProof(proof)) {
      throw new TypeError("The workbook receipt is invalid.");
    }
    const workbook = await response.blob();
    await initiateDownload(workbook, response);
    const acknowledgement = tracker.markPreparedDownloadInitiated(receipt, proof);
    try {
      const result = await acknowledgeDownload(acknowledgement);
      if (result && result.outcome === "acknowledged") {
        tracker.acknowledgeSuccess(receipt);
        return { downloadStarted: true, acknowledged: true, response };
      }
      if (result && result.outcome === "definitive") {
        tracker.abandonPendingAcknowledgement(receipt);
        return {
          downloadStarted: true,
          acknowledged: false,
          definitiveAcknowledgementFailure: true,
          response,
        };
      }
    } catch (_error) {
      // Anchor initiation already happened. Retry only the acknowledgement.
    }
    return { downloadStarted: true, acknowledged: false, response };
  }

  function attachAuthenticatedSnapshotDownloads({
    document: documentObject,
    window: windowObject,
  } = {}) {
    if (!documentObject || !windowObject) return false;
    const forms = Array.from(
      documentObject.querySelectorAll("form[data-authenticated-snapshot-download]")
    );
    if (forms.length === 0
        || typeof windowObject.fetch !== "function"
        || typeof windowObject.FormData !== "function"
        || !windowObject.URL
        || typeof windowObject.URL.createObjectURL !== "function") {
      return false;
    }
    let storageCandidate = null;
    try {
      storageCandidate = windowObject.sessionStorage;
    } catch (_error) {
      storageCandidate = null;
    }
    const tracker = createDownloadTracker({
      storage: storageCandidate,
      storageKey: AUTHENTICATED_STORAGE_KEY,
      now: () => Date.now(),
      uuidFactory: () => windowObject.crypto.randomUUID(),
    });
    const csrf = documentObject.querySelector('meta[name="csrf-token"]')?.content || "";

    async function acknowledge(pending) {
      const response = await windowObject.fetch("/api/downloads/ack", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrf,
        },
        body: JSON.stringify({
          download_event_id: pending.downloadEventId,
          acknowledgement_proof: pending.acknowledgementProof,
        }),
      });
      let body = null;
      try {
        body = await response.json();
      } catch (_error) {
        body = null;
      }
      return classifyAcknowledgementResponse(
        response.status,
        body && body.acknowledged,
      );
    }

    forms.forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const button = form.querySelector('button[type="submit"]');
        const status = form.querySelector("[data-download-status]");
        const originalLabel = button ? button.textContent : "Download snapshot";
        if (button) {
          button.disabled = true;
          button.textContent = "Preparing download…";
        }
        if (status) status.textContent = "Preparing your saved snapshot…";
        try {
          const result = await runAuthenticatedSnapshotDownload({
            tracker,
            retryPendingAcknowledgement: acknowledge,
            prepareSnapshot: () => windowObject.fetch(form.action, {
              method: "POST",
              credentials: "same-origin",
              body: new windowObject.FormData(form),
            }),
            initiateDownload: (blob) => {
              const objectUrl = windowObject.URL.createObjectURL(blob);
              const anchor = documentObject.createElement("a");
              anchor.href = objectUrl;
              anchor.download = "roster-scout-snapshot.xlsx";
              anchor.hidden = true;
              documentObject.body.appendChild(anchor);
              anchor.click();
              anchor.remove();
              windowObject.setTimeout(
                () => windowObject.URL.revokeObjectURL(objectUrl),
                60_000,
              );
            },
            acknowledgeDownload: acknowledge,
          });
          if (!result.downloadStarted) {
            if (result.pendingAcknowledgement) {
              throw new Error("A previous download is still being confirmed.");
            }
            throw new Error("The saved workbook could not be prepared.");
          }
          if (status) status.textContent = "Download started.";
        } catch (_error) {
          if (status) {
            status.textContent = "Download couldn’t start. Try again. If it keeps happening, contact support.";
            status.focus();
          }
        } finally {
          if (button) {
            button.disabled = false;
            button.textContent = originalLabel;
          }
        }
      });
    });
    void retryPendingDownloadAcknowledgement({
      tracker,
      acknowledgeDownload: acknowledge,
    });
    return true;
  }

  return {
    AUTHENTICATED_STORAGE_KEY,
    PROOF_TTL_MS,
    STORAGE_KEY,
    attachAuthenticatedSnapshotDownloads,
    classifyAcknowledgementResponse,
    createSafeStorage,
    createDownloadTracker,
    retryPendingDownloadAcknowledgement,
    runAuthenticatedSnapshotDownload,
    runDownloadSubmission,
  };
});
