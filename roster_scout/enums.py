"""String-backed lifecycle states shared by models and services."""

SNAPSHOT_STATUSES = ("draft", "ready", "partial", "failed")
COLLECTION_CONTRACT_VERSIONS = (
    "legacy-core-v1",
    "quick-season-v1",
    "phase3-detailed-v1",
)
BUILD_PROFILES = ("quick", "detailed")
PUBLISHED_SNAPSHOT_STATUSES = ("ready", "partial")
SECTION_STATUSES = ("pending", "complete", "partial", "unavailable", "failed")
METRIC_STATUSES = (
    "available",
    "missing_input",
    "undefined_denominator",
    "unavailable_source",
    "unavailable_constant",
    "incompatible_input",
)
IDENTITY_STATUSES = ("authoritative", "fallback", "unresolved", "conflict")
JOB_STATUSES = (
    "queued",
    "running",
    "retrying",
    "ready",
    "partial",
    "failed",
    "blocked",
)
ARTIFACT_STATUSES = ("pending", "building", "ready", "failed")
ARTIFACT_TYPES = ("xlsx", "power_bi")
