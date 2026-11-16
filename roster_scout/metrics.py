"""Single Decimal-based registry for auditable NCAA baseball metrics."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping, Sequence

from .enums import METRIC_STATUSES

DEFINITION_VERSION = "ncaa-detailed-1.0"


@dataclass(frozen=True)
class MetricResult:
    value: Decimal | None
    status: str
    reason_code: str | None
    inputs: dict[str, int | Decimal | None]
    sample: dict[str, int | None]
    definition_version: str
    constant_set_version: str | None = None


@dataclass(frozen=True)
class MetricDefinitionSpec:
    code: str
    formula: str
    calculator: Callable[["_Evaluation"], Decimal]
    constant_dependent: bool = False


class _MetricState(Exception):
    def __init__(self, status: str, reason_code: str | None = None):
        super().__init__(status)
        self.status = status
        self.reason_code = reason_code or status


class _Evaluation:
    def __init__(
        self,
        counts: Mapping[str, int | Decimal | None],
        *,
        constant_values: Mapping[str, int | Decimal] | None = None,
    ):
        self.counts = counts
        self.inputs: dict[str, int | Decimal | None] = dict(counts)
        self.constant_values = constant_values

    def count(self, key: str) -> Decimal:
        value = self.counts.get(key)
        self.inputs[key] = value
        if value is None:
            raise _MetricState("missing_input")
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise _MetricState("incompatible_input")
        parsed = Decimal(value)
        if parsed < 0 or parsed != parsed.to_integral_value():
            raise _MetricState("incompatible_input")
        return parsed

    def constant(self, key: str) -> Decimal:
        if self.constant_values is None or key not in self.constant_values:
            raise _MetricState(
                "unavailable_constant", "verified_ncaa_constant_required"
            )
        value = self.constant_values[key]
        if isinstance(value, bool) or not isinstance(value, (int, Decimal, str)):
            raise _MetricState("incompatible_input")
        try:
            return Decimal(value)
        except InvalidOperation:
            raise _MetricState("incompatible_input") from None


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        raise _MetricState("undefined_denominator")
    if denominator < 0:
        raise _MetricState("incompatible_input")
    return numerator / denominator


def _plate_appearances(evaluation: _Evaluation) -> Decimal:
    published = evaluation.counts.get("plate_appearances")
    if published is not None:
        return evaluation.count("plate_appearances")
    return sum(
        (
            evaluation.count("at_bats"),
            evaluation.count("walks"),
            evaluation.count("hit_by_pitch"),
            evaluation.count("sacrifice_flies"),
            evaluation.count("sacrifice_hits"),
        ),
        Decimal(0),
    )


def _singles(evaluation: _Evaluation) -> Decimal:
    value = (
        evaluation.count("hits")
        - evaluation.count("doubles")
        - evaluation.count("triples")
        - evaluation.count("home_runs")
    )
    if value < 0:
        raise _MetricState("incompatible_input")
    return value


def _total_bases(evaluation: _Evaluation) -> Decimal:
    return (
        _singles(evaluation)
        + Decimal(2) * evaluation.count("doubles")
        + Decimal(3) * evaluation.count("triples")
        + Decimal(4) * evaluation.count("home_runs")
    )


def _avg(evaluation: _Evaluation) -> Decimal:
    hits = evaluation.count("hits")
    at_bats = evaluation.count("at_bats")
    if hits > at_bats:
        raise _MetricState("incompatible_input")
    return _ratio(hits, at_bats)


def _obp(evaluation: _Evaluation) -> Decimal:
    hits = evaluation.count("hits")
    at_bats = evaluation.count("at_bats")
    walks = evaluation.count("walks")
    hit_by_pitch = evaluation.count("hit_by_pitch")
    sacrifice_flies = evaluation.count("sacrifice_flies")
    if hits > at_bats:
        raise _MetricState("incompatible_input")
    return _ratio(
        hits + walks + hit_by_pitch,
        at_bats + walks + hit_by_pitch + sacrifice_flies,
    )


def _slg(evaluation: _Evaluation) -> Decimal:
    return _ratio(_total_bases(evaluation), evaluation.count("at_bats"))


def _ops(evaluation: _Evaluation) -> Decimal:
    return _obp(evaluation) + _slg(evaluation)


def _iso(evaluation: _Evaluation) -> Decimal:
    return _slg(evaluation) - _avg(evaluation)


def _batting_walk_rate(evaluation: _Evaluation) -> Decimal:
    return _ratio(evaluation.count("walks"), _plate_appearances(evaluation))


def _batting_strikeout_rate(evaluation: _Evaluation) -> Decimal:
    return _ratio(evaluation.count("strikeouts"), _plate_appearances(evaluation))


def _babip(evaluation: _Evaluation) -> Decimal:
    hits = evaluation.count("hits")
    home_runs = evaluation.count("home_runs")
    numerator = hits - home_runs
    if numerator < 0:
        raise _MetricState("incompatible_input")
    denominator = (
        evaluation.count("at_bats")
        - evaluation.count("strikeouts")
        - home_runs
        + evaluation.count("sacrifice_flies")
    )
    return _ratio(numerator, denominator)


def _woba(evaluation: _Evaluation) -> Decimal:
    singles = _singles(evaluation)
    walks = evaluation.count("walks")
    hit_by_pitch = evaluation.count("hit_by_pitch")
    denominator = (
        evaluation.count("at_bats")
        + walks
        + hit_by_pitch
        + evaluation.count("sacrifice_flies")
    )
    numerator = (
        evaluation.constant("walk") * walks
        + evaluation.constant("hit_by_pitch") * hit_by_pitch
        + evaluation.constant("single") * singles
        + evaluation.constant("double") * evaluation.count("doubles")
        + evaluation.constant("triple") * evaluation.count("triples")
        + evaluation.constant("home_run") * evaluation.count("home_runs")
    )
    return _ratio(numerator, denominator)


def _per_nine(evaluation: _Evaluation, key: str) -> Decimal:
    return _ratio(
        evaluation.count(key) * Decimal(27),
        evaluation.count("innings_outs"),
    )


def _era(evaluation: _Evaluation) -> Decimal:
    return _per_nine(evaluation, "earned_runs")


def _whip(evaluation: _Evaluation) -> Decimal:
    numerator = (
        evaluation.count("walks") + evaluation.count("hits_allowed")
    ) * Decimal(3)
    return _ratio(numerator, evaluation.count("innings_outs"))


def _k_bb(evaluation: _Evaluation) -> Decimal:
    return _ratio(
        evaluation.count("strikeouts"), evaluation.count("walks")
    )


def _pitching_rate(evaluation: _Evaluation, key: str) -> Decimal:
    return _ratio(
        evaluation.count(key), evaluation.count("batters_faced")
    )


def _babip_allowed(evaluation: _Evaluation) -> Decimal:
    hits = evaluation.count("hits_allowed")
    home_runs = evaluation.count("home_runs_allowed")
    numerator = hits - home_runs
    if numerator < 0:
        raise _MetricState("incompatible_input")
    denominator = (
        evaluation.count("opponent_at_bats")
        - evaluation.count("strikeouts")
        - home_runs
        + evaluation.count("opponent_sacrifice_flies")
    )
    return _ratio(numerator, denominator)


def _fip(evaluation: _Evaluation) -> Decimal:
    numerator = (
        Decimal(13) * evaluation.count("home_runs_allowed")
        + Decimal(3)
        * (evaluation.count("walks") + evaluation.count("hit_batters"))
        - Decimal(2) * evaluation.count("strikeouts")
    )
    rate = _ratio(
        numerator * Decimal(3), evaluation.count("innings_outs")
    )
    return rate + evaluation.constant("constant")


METRIC_DEFINITIONS = {
    definition.code: definition
    for definition in (
        MetricDefinitionSpec("pa", "AB + BB + HBP + SF + SH", _plate_appearances),
        MetricDefinitionSpec("avg", "H / AB", _avg),
        MetricDefinitionSpec("obp", "(H + BB + HBP) / (AB + BB + HBP + SF)", _obp),
        MetricDefinitionSpec("slg", "TB / AB", _slg),
        MetricDefinitionSpec("ops", "OBP + SLG", _ops),
        MetricDefinitionSpec("iso", "SLG - AVG", _iso),
        MetricDefinitionSpec("batting_bb_pct", "BB / PA", _batting_walk_rate),
        MetricDefinitionSpec("batting_k_pct", "SO / PA", _batting_strikeout_rate),
        MetricDefinitionSpec("babip", "(H - HR) / (AB - SO - HR + SF)", _babip),
        MetricDefinitionSpec("singles", "H - 2B - 3B - HR", _singles),
        MetricDefinitionSpec("total_bases", "1B + 2*2B + 3*3B + 4*HR", _total_bases),
        MetricDefinitionSpec("woba", "verified NCAA weighted on-base average", _woba, True),
        MetricDefinitionSpec("era", "ER * 27 / outs", _era),
        MetricDefinitionSpec("whip", "(BB + H) * 3 / outs", _whip),
        MetricDefinitionSpec("k_per_9", "SO * 27 / outs", lambda item: _per_nine(item, "strikeouts")),
        MetricDefinitionSpec("bb_per_9", "BB * 27 / outs", lambda item: _per_nine(item, "walks")),
        MetricDefinitionSpec("h_per_9", "H * 27 / outs", lambda item: _per_nine(item, "hits_allowed")),
        MetricDefinitionSpec("hr_per_9", "HR * 27 / outs", lambda item: _per_nine(item, "home_runs_allowed")),
        MetricDefinitionSpec("k_bb", "SO / BB", _k_bb),
        MetricDefinitionSpec("pitching_k_pct", "SO / BF", lambda item: _pitching_rate(item, "strikeouts")),
        MetricDefinitionSpec("pitching_bb_pct", "BB / BF", lambda item: _pitching_rate(item, "walks")),
        MetricDefinitionSpec("babip_allowed", "(H - HR) / (opponent AB - SO - HR + opponent SF)", _babip_allowed),
        MetricDefinitionSpec("fip", "((13*HR + 3*(BB+HBP) - 2*SO) * 3 / outs) + C", _fip, True),
    )
}


def _sample_count(
    counts: Mapping[str, int | Decimal | None], key: str
) -> int | None:
    value = counts.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    parsed = Decimal(value)
    if parsed < 0 or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _sample(counts: Mapping[str, int | Decimal | None]) -> dict[str, int | None]:
    pa = _sample_count(counts, "plate_appearances")
    if pa is None:
        components = tuple(
            _sample_count(counts, key)
            for key in (
                "at_bats",
                "walks",
                "hit_by_pitch",
                "sacrifice_flies",
                "sacrifice_hits",
            )
        )
        if all(value is not None for value in components):
            pa = sum(value for value in components if value is not None)
    return {
        "pa": pa,
        "ab": _sample_count(counts, "at_bats"),
        "bf": _sample_count(counts, "batters_faced"),
        "outs": _sample_count(counts, "innings_outs"),
    }


def _verified_constant_values(
    code: str,
    constant_set: Mapping[str, object] | None,
    *,
    season_id: str | None,
    division: str | None,
) -> tuple[Mapping[str, int | Decimal] | None, str | None]:
    if constant_set is None:
        return None, None
    if (
        constant_set.get("verification_status") != "verified"
        or constant_set.get("metric_code") != code
        or constant_set.get("season_id") != season_id
        or constant_set.get("division") != division
        or not isinstance(constant_set.get("version"), str)
        or not isinstance(constant_set.get("values"), Mapping)
    ):
        return None, None
    values = constant_set["values"]
    return values, str(constant_set["version"])


def calculate_metric(
    code: str,
    counts: Mapping[str, int | Decimal | None],
    *,
    season_id: str | None = None,
    division: str | None = None,
    constant_set: Mapping[str, object] | None = None,
) -> MetricResult:
    """Evaluate one registered metric while retaining exact inputs and sample."""
    try:
        definition = METRIC_DEFINITIONS[code]
    except KeyError as exc:
        raise ValueError(f"Unknown metric code: {code}") from exc
    constant_values, constant_version = _verified_constant_values(
        code,
        constant_set,
        season_id=season_id,
        division=division,
    )
    evaluation = _Evaluation(counts, constant_values=constant_values)
    if definition.constant_dependent and constant_version is None:
        return MetricResult(
            value=None,
            status="unavailable_constant",
            reason_code="verified_ncaa_constant_required",
            inputs=dict(evaluation.inputs),
            sample=_sample(counts),
            definition_version=DEFINITION_VERSION,
            constant_set_version=None,
        )
    try:
        value = definition.calculator(evaluation)
    except _MetricState as state:
        return MetricResult(
            value=None,
            status=state.status,
            reason_code=state.reason_code,
            inputs=dict(evaluation.inputs),
            sample=_sample(counts),
            definition_version=DEFINITION_VERSION,
            constant_set_version=None,
        )
    return MetricResult(
        value=value,
        status="available",
        reason_code=None,
        inputs=dict(evaluation.inputs),
        sample=_sample(counts),
        definition_version=DEFINITION_VERSION,
        constant_set_version=constant_version,
    )


def aggregate_compatible_counts(
    rows: Sequence[Mapping[str, int | Decimal | None]],
) -> dict[str, int | Decimal | None]:
    """Sum compatible count rows without accepting or averaging source rates."""
    keys = set().union(*(row.keys() for row in rows)) if rows else set()
    forbidden = keys.intersection(METRIC_DEFINITIONS)
    if forbidden:
        raise ValueError("Metric rates cannot be aggregated; aggregate counts instead")
    aggregated: dict[str, int | Decimal | None] = {}
    for key in sorted(keys):
        values = [row.get(key) for row in rows]
        if any(value is None for value in values):
            aggregated[key] = None
            continue
        parsed = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
                raise ValueError("Incompatible count input")
            number = Decimal(value)
            if number < 0 or number != number.to_integral_value():
                raise ValueError("Incompatible count input")
            parsed.append(number)
        total = sum(parsed, Decimal(0))
        aggregated[key] = int(total) if all(isinstance(value, int) for value in values) else total
    return aggregated
