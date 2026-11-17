"""Pinned NCAA division-season weights for constant-dependent metrics."""

from __future__ import annotations

import csv
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from .models import MetricConstantSet


SOURCE_PACKAGE = "ncaa_bbStats"
SOURCE_VERSION = "1.4.2"
SOURCE_URL = (
    "https://collegebaseballstatspackage.readthedocs.io/en/latest/_downloads/"
    "ac974bd8989401ff1848c81755253cfc/batting_weights.csv"
)
SOURCE_PAYLOAD_SHA256 = (
    "eb7e4af63cb60455904b6377b361e50f57628b779d52e53ff86fdab26e86e6bb"
)
PITCHING_SOURCE_URL = (
    "https://collegebaseballstatspackage.readthedocs.io/en/latest/_downloads/"
    "a2c5e0a334900d94b762f65682de67d6/pitching_constants.csv"
)
PITCHING_SOURCE_PAYLOAD_SHA256 = (
    "ef3dfcbcd9e7963c6a68740b6965738608fc3d5488bd489bfdc1e0c212e1cc7a"
)
SOURCE_COLLECTED_AT = datetime(2026, 11, 17, tzinfo=UTC)
VERSION = f"ncaa-bbstats-{SOURCE_VERSION}-{SOURCE_PAYLOAD_SHA256[:12]}"
FIP_VERSION = (
    f"ncaa-bbstats-{SOURCE_VERSION}-{PITCHING_SOURCE_PAYLOAD_SHA256[:12]}"
)
DATA_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "ncaa-bbstats-batting-weights-2026.csv"
)
PITCHING_DATA_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "ncaa-bbstats-pitching-constants-2026.csv"
)


@dataclass(frozen=True)
class WobaConstantRow:
    year: int
    division: str
    values: dict[str, str]


@dataclass(frozen=True)
class FipConstantRow:
    year: int
    division: str
    values: dict[str, str]


def _normalize_division(value: str | int) -> str:
    normalized = str(value).strip().casefold()
    if normalized.startswith("d"):
        normalized = normalized[1:]
    if normalized not in {"1", "2", "3"}:
        raise ValueError("division must be 1, 2, or 3")
    return normalized


@lru_cache(maxsize=1)
def _woba_rows() -> dict[tuple[int, str], WobaConstantRow]:
    rows: dict[tuple[int, str], WobaConstantRow] = {}
    with DATA_PATH.open(newline="", encoding="utf-8") as handle:
        for source in csv.DictReader(handle):
            year = int(source["year"])
            division = _normalize_division(source["division"])
            key = (year, division)
            if key in rows:
                raise RuntimeError("duplicate NCAA wOBA constant scope")
            rows[key] = WobaConstantRow(
                year=year,
                division=division,
                values={
                    "walk": source["w_bb"],
                    "hit_by_pitch": source["w_hbp"],
                    "single": source["w_1b"],
                    "double": source["w_2b"],
                    "triple": source["w_3b"],
                    "home_run": source["w_hr"],
                    "woba_scale": source["cwoba_scale"],
                    "league_obp": source["lg_obp"],
                    "league_woba": source["lg_cwoba"],
                    "league_runs_per_pa": source["lg_r_pa"],
                    "team_count": source["n_teams"],
                    "team_season_count": source["n_team_seasons"],
                    "r_squared": source["r2"],
                    "rmse_runs": source["rmse_runs"],
                    "method": source["method"],
                },
            )
    return rows


def woba_constants(year: int, division: str | int) -> WobaConstantRow | None:
    """Return the pinned college-calibrated weights for one exact scope."""
    return _woba_rows().get((int(year), _normalize_division(division)))


@lru_cache(maxsize=1)
def _fip_rows() -> dict[tuple[int, str], FipConstantRow]:
    rows = {}
    with PITCHING_DATA_PATH.open(newline="", encoding="utf-8") as source:
        for item in csv.DictReader(source):
            year = int(item["year"])
            division = _normalize_division(item["division"])
            key = (year, division)
            if key in rows:
                raise RuntimeError("duplicate NCAA FIP constant scope")
            rows[key] = FipConstantRow(
                year=year,
                division=division,
                values={
                    "constant": item["cfip_constant"],
                    "league_era": item["lg_era"],
                    "league_innings": item["lg_ip"],
                    "league_home_runs": item["lg_hr"],
                    "league_walks": item["lg_bb_pit"],
                    "league_hit_batters": item["lg_hbp"],
                    "league_strikeouts": item["lg_so"],
                    "team_count": item["n_teams"],
                    "walk_source": item["bb_allowed_source"],
                    "home_run_source": item["hr_allowed_source"],
                    "method": item["method"],
                },
            )
    return rows


def fip_constants(year: int, division: str | int) -> FipConstantRow | None:
    """Return the pinned college-calibrated FIP constant for one scope."""
    return _fip_rows().get((int(year), _normalize_division(division)))


def ensure_woba_constant_set(
    session,
    *,
    metric_definition_id,
    season_id,
    year: int,
    division: str,
) -> MetricConstantSet | None:
    """Insert the pinned verified set once, while respecting an existing set."""
    existing = session.scalar(
        select(MetricConstantSet).where(
            MetricConstantSet.metric_definition_id == metric_definition_id,
            MetricConstantSet.metric_code == "woba",
            MetricConstantSet.season_id == season_id,
            MetricConstantSet.division == division,
            MetricConstantSet.verification_status == "verified",
        ).limit(1)
    )
    if existing is not None:
        return existing

    constants = woba_constants(year, division)
    if constants is None:
        return None

    constant_id = session.scalar(
        postgresql_insert(MetricConstantSet)
        .values(
            id=uuid.uuid4(),
            metric_definition_id=metric_definition_id,
            metric_code="woba",
            version=VERSION,
            season_id=season_id,
            division=division,
            constant_type="weight_set",
            values_json=constants.values,
            methodology=(
                "College-calibrated wOBA weights regressed by ncaa_bbStats "
                "from NCAA division-season team counting statistics using "
                "weighted least squares, pooled shrinkage, and ordered hit values."
            ),
            source_url=SOURCE_URL,
            source_identifier=(
                f"{SOURCE_PACKAGE}:{SOURCE_VERSION}:batting_weights.csv:"
                f"{year}:d{constants.division}"
            ),
            collected_at=SOURCE_COLLECTED_AT,
            payload_sha256=SOURCE_PAYLOAD_SHA256,
            verification_status="verified",
            verified_at=SOURCE_COLLECTED_AT,
        )
        .on_conflict_do_nothing(
            constraint=(
                "uq_metric_constant_sets_definition_season_division_version"
            )
        )
        .returning(MetricConstantSet.id)
    )
    if constant_id is None:
        return session.scalar(
            select(MetricConstantSet).where(
                MetricConstantSet.metric_definition_id == metric_definition_id,
                MetricConstantSet.season_id == season_id,
                MetricConstantSet.division == division,
                MetricConstantSet.version == VERSION,
            )
        )
    return session.get(MetricConstantSet, constant_id)


def ensure_fip_constant_set(
    session,
    *,
    metric_definition_id,
    season_id,
    year: int,
    division: str,
) -> MetricConstantSet | None:
    """Insert the pinned verified FIP set once for the exact NCAA scope."""
    existing = session.scalar(
        select(MetricConstantSet).where(
            MetricConstantSet.metric_definition_id == metric_definition_id,
            MetricConstantSet.metric_code == "fip",
            MetricConstantSet.season_id == season_id,
            MetricConstantSet.division == division,
            MetricConstantSet.verification_status == "verified",
        ).limit(1)
    )
    if existing is not None:
        return existing

    constants = fip_constants(year, division)
    if constants is None:
        return None
    constant_id = session.scalar(
        postgresql_insert(MetricConstantSet)
        .values(
            id=uuid.uuid4(),
            metric_definition_id=metric_definition_id,
            metric_code="fip",
            version=FIP_VERSION,
            season_id=season_id,
            division=division,
            constant_type="additive_constant",
            values_json=constants.values,
            methodology=(
                "College-calibrated FIP additive constant derived by "
                "ncaa_bbStats from NCAA division-season league totals."
            ),
            source_url=PITCHING_SOURCE_URL,
            source_identifier=(
                f"{SOURCE_PACKAGE}:{SOURCE_VERSION}:pitching_constants.csv:"
                f"{year}:d{constants.division}"
            ),
            collected_at=SOURCE_COLLECTED_AT,
            payload_sha256=PITCHING_SOURCE_PAYLOAD_SHA256,
            verification_status="verified",
            verified_at=SOURCE_COLLECTED_AT,
        )
        .on_conflict_do_nothing(
            constraint=(
                "uq_metric_constant_sets_definition_season_division_version"
            )
        )
        .returning(MetricConstantSet.id)
    )
    if constant_id is None:
        return session.scalar(
            select(MetricConstantSet).where(
                MetricConstantSet.metric_definition_id == metric_definition_id,
                MetricConstantSet.season_id == season_id,
                MetricConstantSet.division == division,
                MetricConstantSet.version == FIP_VERSION,
            )
        )
    return session.get(MetricConstantSet, constant_id)
