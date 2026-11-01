"""String-backed lifecycle states shared by models and services."""

SNAPSHOT_STATUSES = ("draft", "ready", "partial", "failed")
PUBLISHED_SNAPSHOT_STATUSES = ("ready", "partial")
SECTION_STATUSES = ("pending", "complete", "unavailable", "failed")
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
