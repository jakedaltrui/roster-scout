#!/usr/bin/env python3
"""Capture minimal, sanitized NCAA split evidence through the existing browser seam."""

from __future__ import annotations

import argparse
import hashlib
import html as html_module
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup


if __file__ == "<stdin>":
    PROJECT_ROOT = Path.cwd().resolve()
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURE_ROOT = (PROJECT_ROOT / "tests" / "fixtures" / "ncaa_detail").resolve()
NCAA_ORIGIN = "https://stats.ncaa.org"
SANITIZER_VERSION = "phase3-ncaa-split-sanitizer-1"
CONTROL_CONTRACT_VERSION = "ncaa-split-controls-v1"
PREFLIGHT_FAILURE = "phase3_browserless_unavailable"
CAPTURE_FAILURE = "phase3_ncaa_capture_unavailable"
INVALID_FAILURE = "phase3_capture_invalid"
MAX_RAW_PAGE_BYTES = 2 * 1024 * 1024
RESULT_CONTRACT_VERSION = "ncaa-split-result-v1"

CANONICAL_LABELS = {
    "batting": (
        "vs_lhp",
        "vs_rhp",
        "runners_on",
        "bases_empty",
        "bases_loaded",
        "runners_in_scoring_position",
        "two_outs",
    ),
    "pitching": (
        "vs_lhb",
        "vs_rhb",
        "runners_on",
        "bases_empty",
        "bases_loaded",
        "runners_in_scoring_position",
        "two_outs",
    ),
}

_LABEL_PATTERNS = {
    "vs_lhp": (
        r"\bvs\.?\s*(?:lhp|left[- ]handed pitchers?)\b",
        r"\bversus\s+left[- ]handed pitchers?\b",
    ),
    "vs_rhp": (
        r"\bvs\.?\s*(?:rhp|right[- ]handed pitchers?)\b",
        r"\bversus\s+right[- ]handed pitchers?\b",
    ),
    "vs_lhb": (
        r"\bvs\.?\s*(?:lhb|left[- ]handed batters?|left[- ]handed hitters?)\b",
        r"\bversus\s+left[- ]handed (?:batters?|hitters?)\b",
    ),
    "vs_rhb": (
        r"\bvs\.?\s*(?:rhb|right[- ]handed batters?|right[- ]handed hitters?)\b",
        r"\bversus\s+right[- ]handed (?:batters?|hitters?)\b",
    ),
    "runners_on": (
        r"\brunners?\s+on\b",
        r"\bmen\s+on\b",
        r"\bhits?[- ]ab\s+with\s+runners?\s+o(?:b|n)\b",
    ),
    "bases_empty": (r"\bbases?\s+empty\b", r"\bno\s+runners?\s+on\b"),
    "bases_loaded": (r"\bbases?\s+loaded\b",),
    "runners_in_scoring_position": (
        r"\brunners?\s+in\s+scoring\s+position\b",
        r"\brisp\b",
        r"\b(?:h|hits?)[- ]ab\s+scorepos\b",
    ),
    "two_outs": (
        r"\b(?:two|2)\s+outs?\b",
        r"\bwith\s+(?:two|2)\s+outs?\b",
        r"\bw2outs?\b",
    ),
}

_SAFE_STATUS = {"candidate", "source_not_published", "source_identifier_not_found"}
_CANDIDATE_PARAM = re.compile(r"(?:split|situat|summary|stat[_-]?seq|context)", re.I)


class CaptureUnavailable(RuntimeError):
    """Internal fixed-category signal; its message is never source text."""

    def __init__(self, category: str, *, stage: str | None = None):
        super().__init__(category)
        self.stage = stage


@dataclass(frozen=True)
class CaptureLimits:
    max_pages: int = 2
    timeout_seconds: int = 180
    max_output_bytes: int = 256 * 1024

    def validate(self) -> None:
        if not 1 <= self.max_pages <= 8:
            raise ValueError("capture page limit is invalid")
        if not 15 <= self.timeout_seconds <= 300:
            raise ValueError("capture timeout is invalid")
        if not 4 * 1024 <= self.max_output_bytes <= 1024 * 1024:
            raise ValueError("capture output limit is invalid")


@dataclass(frozen=True)
class CaptureRequest:
    season_id: str
    season_team_id: str
    division: str
    output_dir: Path
    limits: CaptureLimits


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise ValueError("capture arguments are invalid")


def _numeric(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized.isdigit():
        raise ValueError(f"{field} must be numeric")
    return normalized


def _safe_source_path(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or parsed.username or parsed.password:
        raise ValueError("source path must be relative")
    if not re.fullmatch(r"/teams/\d+/season_to_date_stats", parsed.path):
        raise ValueError("source path is not approved")
    return parsed.path


def build_ncaa_url(season_team_id: str, category_id: str) -> str:
    team = _numeric(season_team_id, "season team ID")
    category = _numeric(category_id, "category ID")
    return (
        f"{NCAA_ORIGIN}/teams/{team}/season_to_date_stats"
        f"?year_stat_category_id={category}"
    )


def validate_capture_request(
    *,
    season_id: str,
    season_team_id: str,
    division: str,
    output_dir: str | Path,
    limits: CaptureLimits,
) -> CaptureRequest:
    """Validate every operator-controlled value before any external client exists."""
    season = _numeric(season_id, "season ID")
    team = _numeric(season_team_id, "season team ID")
    division_value = str(division or "").strip().lower().removeprefix("d")
    if division_value not in {"1", "2", "3"}:
        raise ValueError("division must be 1, 2, or 3")
    limits.validate()
    expected = (FIXTURE_ROOT / f"d{division_value}").resolve()
    candidate = Path(output_dir)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    candidate = candidate.resolve()
    if candidate != expected:
        raise ValueError("output directory is outside its division fixture root")
    return CaptureRequest(season, team, division_value, candidate, limits)


def _normalize_label(text: str, stat_type: str) -> str | None:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    for label in CANONICAL_LABELS[stat_type]:
        if any(re.search(pattern, normalized, re.I) for pattern in _LABEL_PATTERNS[label]):
            return label
    return None


def _candidate_identifier(element, *, excluded: set[str]) -> str | None:
    direct_values = []
    for name, value in element.attrs.items():
        if name.lower().startswith("on"):
            continue
        if isinstance(value, list):
            direct_values.extend(str(item) for item in value)
        else:
            direct_values.append(str(value))
    for value in direct_values:
        if value.isdigit() and value not in excluded:
            return value
    href = element.get("href") or ""
    parsed = urlparse(href)
    if parsed.scheme and (parsed.scheme != "https" or parsed.netloc != "stats.ncaa.org"):
        return None
    for key, values in parse_qs(parsed.query, keep_blank_values=False).items():
        if not _CANDIDATE_PARAM.search(key):
            continue
        for value in values:
            if value.isdigit() and value not in excluded:
                return value
    return None


def discover_split_controls(
    raw_html: str,
    *,
    season_id: str,
    season_team_id: str,
    stat_type: str,
) -> list[dict]:
    if stat_type not in CANONICAL_LABELS:
        raise ValueError("stat type is invalid")
    if not isinstance(raw_html, str) or len(raw_html.encode("utf-8")) > MAX_RAW_PAGE_BYTES:
        raise CaptureUnavailable(CAPTURE_FAILURE)
    soup = BeautifulSoup(raw_html, "html.parser")
    excluded = {season_id, season_team_id}
    found: dict[str, str | None] = {}
    for element in soup.find_all(["option", "a", "button", "input", "label"]):
        visible = element.get_text(" ", strip=True)
        if element.name == "input":
            visible = " ".join(filter(None, (
                visible,
                element.get("value", ""),
                element.get("title", ""),
                element.get("aria-label", ""),
            )))
        label = _normalize_label(visible, stat_type)
        if label is None:
            continue
        identifier = _candidate_identifier(element, excluded=excluded)
        if identifier is None and element.parent is not None:
            identifier = _candidate_identifier(element.parent, excluded=excluded)
        if label not in found or (found[label] is None and identifier is not None):
            found[label] = identifier

    observations = []
    for label in CANONICAL_LABELS[stat_type]:
        identifier = found.get(label)
        status = "candidate" if identifier else "source_identifier_not_found"
        observations.append({
            "canonical_label": label,
            "identifier": identifier,
            "stat_type": stat_type,
            "status": status,
        })
    return observations


def _render_controls_section(
    *,
    season_id: str,
    season_team_id: str,
    division: str,
    stat_type: str,
    source_path: str,
    observations: list[dict],
    source_stat_category_id: str | None = None,
) -> str:
    attrs = {
        "data-division": f"d{division}",
        "data-season-id": season_id,
        "data-season-team-id": season_team_id,
        "data-source-path": source_path,
        "data-stat-type": stat_type,
    }
    if source_stat_category_id is not None:
        attrs["data-source-stat-category-id"] = _numeric(
            source_stat_category_id, "source stat category ID"
        )
    attr_text = " ".join(
        f'{key}="{html_module.escape(value, quote=True)}"'
        for key, value in sorted(attrs.items())
    )
    lines = [f"  <section {attr_text}>"]
    for item in observations:
        control_attrs = {
            "data-canonical-label": item["canonical_label"],
            "data-status": item["status"],
            "data-stat-type": item["stat_type"],
        }
        if item.get("identifier"):
            control_attrs["data-identifier"] = item["identifier"]
        control_text = " ".join(
            f'{key}="{html_module.escape(value, quote=True)}"'
            for key, value in sorted(control_attrs.items())
        )
        lines.append(f"    <control {control_text}></control>")
    lines.append("  </section>")
    return "\n".join(lines)


def sanitize_split_controls(
    raw_html: str,
    *,
    season_id: str,
    season_team_id: str,
    division: str,
    stat_type: str,
    source_path: str,
) -> str:
    season = _numeric(season_id, "season ID")
    team = _numeric(season_team_id, "season team ID")
    division_value = str(division).lower().removeprefix("d")
    if division_value not in {"1", "2", "3"}:
        raise ValueError("division is invalid")
    path = _safe_source_path(source_path)
    observations = discover_split_controls(
        raw_html,
        season_id=season,
        season_team_id=team,
        stat_type=stat_type,
    )
    return (
        f'<split-controls data-sanitizer-version="{SANITIZER_VERSION}">\n'
        + _render_controls_section(
            season_id=season,
            season_team_id=team,
            division=division_value,
            stat_type=stat_type,
            source_path=path,
            observations=observations,
        )
        + "\n</split-controls>\n"
    )


def build_fixture_manifest(
    *,
    division: str,
    season_id: str,
    season_team_id: str,
    captured_at: str,
    controls_sha256: str,
    control_sources: list[dict],
    observations: list[dict],
) -> dict:
    safe_observations = []
    for item in observations:
        status = item.get("status")
        if status not in _SAFE_STATUS:
            raise ValueError("observation status is invalid")
        identifier = item.get("identifier")
        if identifier is not None:
            identifier = _numeric(identifier, "split identifier")
        stat_type = item.get("stat_type")
        label = item.get("canonical_label")
        if stat_type not in CANONICAL_LABELS or label not in CANONICAL_LABELS[stat_type]:
            raise ValueError("observation identity is invalid")
        safe_observations.append({
            "canonical_label": label,
            "identifier": identifier,
            "source_path": _safe_source_path(item["source_path"]),
            "source_stat_category_id": _numeric(
                item["source_stat_category_id"], "source stat category ID"
            ),
            "stat_type": stat_type,
            "status": status,
        })
    if not re.fullmatch(r"[0-9a-f]{64}", controls_sha256):
        raise ValueError("controls checksum is invalid")
    safe_sources = []
    seen_types = set()
    for source in control_sources:
        stat_type = source.get("stat_type")
        headers = source.get("normalized_headers")
        if stat_type not in CANONICAL_LABELS or stat_type in seen_types:
            raise ValueError("control source type is invalid")
        if not isinstance(headers, list) or not headers or not all(
            isinstance(value, str) and value and len(value) <= 40 for value in headers
        ):
            raise ValueError("control source headers are invalid")
        seen_types.add(stat_type)
        safe_sources.append({
            "normalized_headers": headers,
            "source_path": _safe_source_path(source["source_path"]),
            "source_stat_category_id": _numeric(
                source["source_stat_category_id"], "source stat category ID"
            ),
            "stat_type": stat_type,
        })
    if seen_types != set(CANONICAL_LABELS):
        raise ValueError("control source types are incomplete")
    return {
        "captured_at": captured_at,
        "control_contract_version": CONTROL_CONTRACT_VERSION,
        "control_sources": sorted(safe_sources, key=lambda item: item["stat_type"]),
        "controls_sha256": controls_sha256,
        "division": f"d{str(division).lower().removeprefix('d')}",
        "observations": sorted(
            safe_observations,
            key=lambda item: (item["stat_type"], item["canonical_label"]),
        ),
        "sanitizer_version": SANITIZER_VERSION,
        "season_id": _numeric(season_id, "season ID"),
        "season_team_id": _numeric(season_team_id, "season team ID"),
    }


def _normalized_base_headers(raw_html: str, stat_type: str) -> list[str]:
    import roster_scraper as rs

    if stat_type not in rs.NCAA_CORE_HEADER_SIGNATURES:
        raise CaptureUnavailable(CAPTURE_FAILURE)
    soup = BeautifulSoup(raw_html, "html.parser")
    table = soup.select_one("#stat_grid")
    if table is None:
        raise CaptureUnavailable(CAPTURE_FAILURE)
    header_rows = table.select("thead tr")
    header_row = header_rows[-1] if header_rows else table.find("tr")
    if header_row is None:
        raise CaptureUnavailable(CAPTURE_FAILURE)
    headers = [
        re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).casefold()
        for cell in header_row.find_all(["th", "td"], recursive=False)
    ]
    required = rs.NCAA_CORE_HEADER_SIGNATURES[stat_type]["required_headers"]
    if any(headers.count(value) != 1 for value in required):
        raise CaptureUnavailable(CAPTURE_FAILURE)
    if any(not value or len(value) > 40 for value in headers):
        raise CaptureUnavailable(CAPTURE_FAILURE)
    return headers


def _season_categories(season_id: str) -> dict[str, str]:
    import roster_scraper as rs

    matches = [
        row for row in rs._load_ncaa_season_rows()
        if str(row.get("id") or "") == season_id
    ]
    if len(matches) != 1:
        raise CaptureUnavailable(CAPTURE_FAILURE)
    categories = {
        "batting": _numeric(matches[0].get("batting_id"), "batting category ID"),
        "pitching": _numeric(matches[0].get("pitching_id"), "pitching category ID"),
    }
    return categories


def _validate_browserless_configuration() -> None:
    token = os.environ.get("BROWSERLESS_TOKEN")
    if not token:
        raise CaptureUnavailable(PREFLIGHT_FAILURE)
    if os.environ.get("NCAA_BROWSER_PROVIDER") != "browserless":
        raise CaptureUnavailable(PREFLIGHT_FAILURE)
    endpoint = os.environ.get(
        "BROWSERLESS_UNBLOCK_ENDPOINT",
        "https://production-sfo.browserless.io/chromium/unblock",
    )
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".browserless.io")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise CaptureUnavailable(PREFLIGHT_FAILURE)


def _bounded_fetch(
    urls: dict[str, str],
    limits: CaptureLimits,
    *,
    page_consumer=None,
) -> dict[str, object]:
    limits.validate()
    if len(urls) > limits.max_pages:
        raise ValueError("capture page count exceeds the validated limit")
    for url in urls.values():
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "stats.ncaa.org":
            raise ValueError("capture source host is not approved")
        if not re.fullmatch(r"/teams/\d+/season_to_date_stats", parsed.path):
            raise ValueError("capture source path is not approved")

    import roster_scraper as rs

    old_timeout = os.environ.get("BROWSERLESS_TIMEOUT_MILLISECONDS")
    old_verify = os.environ.get("NCAA_BROWSER_VERIFY_TIMEOUT_SECONDS")
    os.environ["BROWSERLESS_TIMEOUT_MILLISECONDS"] = str(
        min(limits.timeout_seconds * 1000, 120000)
    )
    os.environ["NCAA_BROWSER_VERIFY_TIMEOUT_SECONDS"] = str(
        min(limits.timeout_seconds, 45)
    )
    try:
        pages = rs._fetch_ncaa_pages(urls, page_consumer=page_consumer)
    except Exception as exc:
        raise CaptureUnavailable(CAPTURE_FAILURE, stage="bounded_fetch") from exc
    finally:
        if old_timeout is None:
            os.environ.pop("BROWSERLESS_TIMEOUT_MILLISECONDS", None)
        else:
            os.environ["BROWSERLESS_TIMEOUT_MILLISECONDS"] = old_timeout
        if old_verify is None:
            os.environ.pop("NCAA_BROWSER_VERIFY_TIMEOUT_SECONDS", None)
        else:
            os.environ["NCAA_BROWSER_VERIFY_TIMEOUT_SECONDS"] = old_verify
    if set(pages) != set(urls):
        raise CaptureUnavailable(CAPTURE_FAILURE)
    for raw in pages.values():
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_RAW_PAGE_BYTES:
            raise CaptureUnavailable(CAPTURE_FAILURE)
    return pages


def preflight(*, limits: CaptureLimits | None = None) -> None:
    limits = limits or CaptureLimits(max_pages=1)
    limits.validate()
    _validate_browserless_configuration()
    categories = _season_categories("17040")
    url = build_ncaa_url("614606", categories["batting"])
    _bounded_fetch({"preflight": url}, limits)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    )
    try:
        with handle:
            handle.write(content)
        os.replace(handle.name, path)
    except Exception:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _capture_control_bundle(request: CaptureRequest) -> dict:
    _validate_browserless_configuration()
    categories = _season_categories(request.season_id)
    urls = {
        stat_type: build_ncaa_url(request.season_team_id, category_id)
        for stat_type, category_id in categories.items()
    }
    pages = _bounded_fetch(urls, request.limits)
    captured_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    sections = []
    observations = []
    control_sources = []
    source_path = f"/teams/{request.season_team_id}/season_to_date_stats"
    for stat_type in ("batting", "pitching"):
        found = discover_split_controls(
            pages[stat_type],
            season_id=request.season_id,
            season_team_id=request.season_team_id,
            stat_type=stat_type,
        )
        for item in found:
            observations.append({
                **item,
                "source_path": source_path,
                "source_stat_category_id": categories[stat_type],
            })
        control_sources.append({
            "normalized_headers": _normalized_base_headers(pages[stat_type], stat_type),
            "source_path": source_path,
            "source_stat_category_id": categories[stat_type],
            "stat_type": stat_type,
        })
        sections.append(_render_controls_section(
            season_id=request.season_id,
            season_team_id=request.season_team_id,
            division=request.division,
            stat_type=stat_type,
            source_path=source_path,
            observations=found,
            source_stat_category_id=categories[stat_type],
        ))
    controls = (
        f'<split-controls data-captured-at="{captured_at}" '
        f'data-sanitizer-version="{SANITIZER_VERSION}">\n'
        + "\n".join(sections)
        + "\n</split-controls>\n"
    )
    if len(controls.encode("utf-8")) > request.limits.max_output_bytes:
        raise CaptureUnavailable(CAPTURE_FAILURE)
    checksum = hashlib.sha256(controls.encode("utf-8")).hexdigest()
    manifest = build_fixture_manifest(
        division=request.division,
        season_id=request.season_id,
        season_team_id=request.season_team_id,
        captured_at=captured_at,
        controls_sha256=checksum,
        control_sources=control_sources,
        observations=observations,
    )
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if len(manifest_text.encode("utf-8")) > request.limits.max_output_bytes:
        raise CaptureUnavailable(CAPTURE_FAILURE)
    return {
        "fixture_manifest.json": manifest_text,
        "split_controls.html": controls,
    }


def capture_controls(
    *,
    season_id: str,
    season_team_id: str,
    division: str,
    output_dir: str | Path,
    limits: CaptureLimits | None = None,
    write: bool = True,
) -> dict:
    request = validate_capture_request(
        season_id=season_id,
        season_team_id=season_team_id,
        division=division,
        output_dir=output_dir,
        limits=limits or CaptureLimits(),
    )
    bundle = _capture_control_bundle(request)
    if write:
        for filename, content in bundle.items():
            _atomic_write(request.output_dir / filename, content)
    return bundle


def _load_control_manifest(request: CaptureRequest) -> dict:
    inline = os.environ.get("PHASE3_CONTROL_MANIFEST_JSON")
    try:
        manifest = (
            json.loads(inline)
            if inline is not None
            else json.loads(
                (request.output_dir / "fixture_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
        )
    except (OSError, ValueError, TypeError) as exc:
        raise CaptureUnavailable(CAPTURE_FAILURE) from exc
    if (
        manifest.get("season_id") != request.season_id
        or manifest.get("season_team_id") != request.season_team_id
        or manifest.get("division") != f"d{request.division}"
        or manifest.get("control_contract_version") != CONTROL_CONTRACT_VERSION
    ):
        raise CaptureUnavailable(CAPTURE_FAILURE)
    return manifest


def _representative_candidates(manifest: dict) -> dict[str, dict]:
    representative_labels = {"batting": "vs_lhp", "pitching": "vs_lhb"}
    observations = {
        (item.get("stat_type"), item.get("canonical_label")): item
        for item in manifest.get("observations", [])
        if isinstance(item, dict)
    }
    selected = {}
    for stat_type, label in representative_labels.items():
        item = observations.get((stat_type, label))
        if (
            not item
            or item.get("status") != "candidate"
            or not str(item.get("identifier") or "").isdigit()
            or not str(item.get("source_stat_category_id") or "").isdigit()
        ):
            selected[stat_type] = {
                "canonical_label": label,
                "identifier": None,
                "source_stat_category_id": None,
                "status": "source_identifier_not_found",
            }
        else:
            selected[stat_type] = dict(item)
    return selected


def _interactive_split_consumer(candidates: dict[str, dict]):
    def consume(*, key, page, source_url, html):
        candidate = candidates[key]
        identifier = candidate.get("identifier")
        if not identifier:
            return html
        selector = f".sport_stat_menu_option_{identifier}"
        try:
            control = page.locator(selector)
            if control.count() != 1:
                raise CaptureUnavailable(CAPTURE_FAILURE)
            if _normalize_label(control.inner_text(), key) != candidate["canonical_label"]:
                raise CaptureUnavailable(CAPTURE_FAILURE)
            if control.get_attribute("data-sport-stat-id") != identifier:
                raise CaptureUnavailable(CAPTURE_FAILURE)
            control.evaluate("element => element.click()")
            page.wait_for_timeout(250)
            return page.content()
        except CaptureUnavailable:
            raise
        except Exception as exc:
            raise CaptureUnavailable(CAPTURE_FAILURE) from exc

    return consume


def _player_row_for_split_node(node):
    row = node if node.name == "tr" else node.find_parent("tr")
    if row is None:
        return None
    if re.fullmatch(r"player_\d+", str(row.get("id") or "")):
        return row
    previous = row.find_previous_sibling("tr")
    while previous is not None:
        if re.fullmatch(r"player_\d+", str(previous.get("id") or "")):
            return previous
        previous = previous.find_previous_sibling("tr")
    return None


def _result_rows(raw_html: str, identifier: str, stat_type: str) -> list[dict]:
    soup = BeautifulSoup(raw_html, "html.parser")
    nodes = soup.select(f".sport_stat_{identifier}")
    if not nodes:
        return []
    headers = _normalized_base_headers(raw_html, stat_type)
    ab_header = "ab" if stat_type == "batting" else "p-oab"
    if headers.count("h") != 1 or headers.count(ab_header) != 1:
        raise CaptureUnavailable(CAPTURE_FAILURE, stage="rows_headers")
    rows = {}
    for node in nodes:
        stat_row = node if node.name == "tr" else node.find_parent("tr")
        if stat_row is None:
            continue
        player_row = _player_row_for_split_node(node)
        if player_row is None:
            continue
        link = player_row.find("a", href=re.compile(r"/players/\d+"))
        if link is not None:
            parsed = urlparse(link.get("href", ""))
            if parsed.scheme or parsed.netloc or not re.fullmatch(r"/players/\d+", parsed.path):
                raise CaptureUnavailable(CAPTURE_FAILURE, stage="rows_player_path")
            player_path = parsed.path
        else:
            row_identity = re.fullmatch(
                r"player_(\d+)", str(player_row.get("id") or "")
            )
            if row_identity is None:
                raise CaptureUnavailable(CAPTURE_FAILURE, stage="rows_player_link")
            player_path = f"/players/{row_identity.group(1)}"
        cells = stat_row.find_all(["th", "td"], recursive=False)
        offset = len(headers) - len(cells)
        h_index = headers.index("h") - offset
        ab_index = headers.index(ab_header) - offset
        if (
            offset < 0
            or h_index < 0
            or ab_index < 0
            or h_index >= len(cells)
            or ab_index >= len(cells)
        ):
            raise CaptureUnavailable(CAPTURE_FAILURE, stage="rows_shape")
        h_text = cells[h_index].get_text(" ", strip=True)
        ab_text = cells[ab_index].get_text(" ", strip=True)
        missing_values = {"", "-", "--"}
        if h_text in missing_values and ab_text in missing_values:
            continue
        if h_text in missing_values and ab_text.isdigit():
            h_text = "0"
        if ab_text in missing_values and h_text == "0":
            ab_text = "0"
        if not h_text.isdigit() or not ab_text.isdigit():
            raise CaptureUnavailable(CAPTURE_FAILURE, stage="rows_value_shape")
        hits, at_bats = int(h_text), int(ab_text)
        if hits < 0 or at_bats < 0 or hits > at_bats:
            raise CaptureUnavailable(CAPTURE_FAILURE, stage="rows_values")
        rows[player_path] = {"player_path": player_path, "h": hits, "ab": at_bats}
    if nodes and not rows:
        raise CaptureUnavailable(CAPTURE_FAILURE, stage="rows_no_pairs")
    return [rows[key] for key in sorted(rows, key=lambda value: int(value.rsplit("/", 1)[1]))]


def _render_result_fixture(
    *,
    request: CaptureRequest,
    stat_type: str,
    candidate: dict,
    raw_html: str | None,
    captured_at: str,
) -> str:
    label = candidate["canonical_label"]
    identifier = candidate.get("identifier")
    category_id = candidate.get("source_stat_category_id")
    source_path = f"/teams/{request.season_team_id}/season_to_date_stats"
    source_checksum = (
        hashlib.sha256(raw_html.encode("utf-8")).hexdigest()
        if raw_html is not None
        else hashlib.sha256(
            json.dumps(candidate, sort_keys=True).encode("utf-8")
        ).hexdigest()
    )
    attrs = {
        "data-canonical-label": label,
        "data-captured-at": captured_at,
        "data-division": f"d{request.division}",
        "data-result-contract-version": RESULT_CONTRACT_VERSION,
        "data-season-id": request.season_id,
        "data-season-team-id": request.season_team_id,
        "data-source-path": source_path,
        "data-source-payload-sha256": source_checksum,
        "data-stat-type": stat_type,
    }
    if identifier:
        attrs["data-identifier"] = _numeric(identifier, "split identifier")
    if category_id:
        attrs["data-source-stat-category-id"] = _numeric(
            category_id, "source stat category ID"
        )
    if not identifier or raw_html is None:
        attrs["data-status"] = "unavailable"
        reason = candidate.get("status") or "source_identifier_not_found"
        if reason not in {"source_not_published", "source_identifier_not_found"}:
            reason = "source_identifier_not_found"
        attr_text = " ".join(
            f'{key}="{html_module.escape(value, quote=True)}"'
            for key, value in sorted(attrs.items())
        )
        return (
            f"<split-result {attr_text}>\n"
            f'  <source-absence reason="{reason}"></source-absence>\n'
            "</split-result>\n"
        )

    try:
        _normalized_base_headers(raw_html, stat_type)
    except Exception as exc:
        raise CaptureUnavailable(
            CAPTURE_FAILURE, stage=f"render_{stat_type}_headers"
        ) from exc
    try:
        rows = _result_rows(raw_html, identifier, stat_type)
    except CaptureUnavailable as exc:
        if exc.stage:
            raise
        raise CaptureUnavailable(
            CAPTURE_FAILURE, stage=f"render_{stat_type}_rows"
        ) from exc
    except Exception as exc:
        raise CaptureUnavailable(
            CAPTURE_FAILURE, stage=f"render_{stat_type}_rows"
        ) from exc
    if not rows:
        attrs["data-status"] = "unavailable"
        attr_text = " ".join(
            f'{key}="{html_module.escape(value, quote=True)}"'
            for key, value in sorted(attrs.items())
        )
        return (
            f"<split-result {attr_text}>\n"
            '  <source-absence reason="source_not_published"></source-absence>\n'
            "</split-result>\n"
        )

    attrs["data-status"] = "complete"
    attr_text = " ".join(
        f'{key}="{html_module.escape(value, quote=True)}"'
        for key, value in sorted(attrs.items())
    )
    lines = [
        f"<split-result {attr_text}>",
        '  <table id="split_result_grid">',
        "    <thead><tr><th>Player</th><th>H</th><th>AB</th></tr></thead>",
        "    <tbody>",
    ]
    for index, row in enumerate(rows, start=1):
        lines.append(
            "      <tr>"
            f'<td><a href="{row["player_path"]}">Player {index}</a></td>'
            f'<td>{row["h"]}</td><td>{row["ab"]}</td>'
            "</tr>"
        )
    lines.extend(["    </tbody>", "  </table>", "</split-result>"])
    return "\n".join(lines) + "\n"


def certify_result_fixture(
    fixture_html: str,
    *,
    expected_season_id: str,
    expected_season_team_id: str,
    expected_division: str,
    expected_stat_type: str,
    expected_canonical_label: str,
    expected_identifier: str | None,
    expected_source_stat_category_id: str | None,
) -> dict:
    soup = BeautifulSoup(fixture_html, "html.parser")
    root = soup.find("split-result")
    if root is None or soup.find("script") is not None:
        raise ValueError("result fixture structure is invalid")
    expected = {
        "data-canonical-label": expected_canonical_label,
        "data-division": f"d{str(expected_division).lower().removeprefix('d')}",
        "data-result-contract-version": RESULT_CONTRACT_VERSION,
        "data-season-id": _numeric(expected_season_id, "season ID"),
        "data-season-team-id": _numeric(expected_season_team_id, "season team ID"),
        "data-source-path": (
            f"/teams/{_numeric(expected_season_team_id, 'season team ID')}"
            "/season_to_date_stats"
        ),
        "data-stat-type": expected_stat_type,
    }
    if expected_identifier is not None:
        expected["data-identifier"] = _numeric(expected_identifier, "split identifier")
    if expected_source_stat_category_id is not None:
        expected["data-source-stat-category-id"] = _numeric(
            expected_source_stat_category_id, "source stat category ID"
        )
    if any(root.get(key) != value for key, value in expected.items()):
        raise ValueError("result fixture identity mismatch")
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", root.get("data-captured-at", "")):
        raise ValueError("result fixture capture time is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", root.get("data-source-payload-sha256", "")):
        raise ValueError("result fixture checksum is invalid")
    forbidden = ("token=", "cookie", "authorization", "browserless", "@")
    lowered = fixture_html.casefold()
    if any(value in lowered for value in forbidden):
        raise ValueError("result fixture contains forbidden content")

    status = root.get("data-status")
    if status == "unavailable":
        absence = root.find("source-absence", recursive=False)
        table = root.find("table")
        if absence is None or table is not None:
            raise ValueError("absence fixture structure is invalid")
        reason = absence.get("reason")
        if reason not in {"source_not_published", "source_identifier_not_found"}:
            raise ValueError("absence fixture reason is invalid")
        return {"status": status, "reason": reason, "row_count": 0}
    if status != "complete":
        raise ValueError("result fixture status is invalid")
    table = root.find("table", id="split_result_grid", recursive=False)
    if table is None:
        raise ValueError("result fixture grid is missing")
    header_row = table.select_one("thead tr")
    headers = [
        re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).casefold()
        for cell in header_row.find_all(["th", "td"], recursive=False)
    ] if header_row is not None else []
    if headers != ["player", "h", "ab"]:
        raise ValueError("result fixture headers are invalid")
    rows = table.select("tbody tr")
    if not rows:
        raise ValueError("complete result fixture is empty")
    for index, row in enumerate(rows, start=1):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) != 3:
            raise ValueError("result fixture row shape is invalid")
        links = cells[0].find_all("a", href=True)
        if (
            len(links) != 1
            or not re.fullmatch(r"/players/\d+", links[0].get("href", ""))
            or links[0].get_text(" ", strip=True) != f"Player {index}"
        ):
            raise ValueError("result fixture player link is invalid")
        if not all(cell.get_text(" ", strip=True).isdigit() for cell in cells[1:]):
            raise ValueError("result fixture values are invalid")
        if int(cells[1].get_text(strip=True)) > int(cells[2].get_text(strip=True)):
            raise ValueError("result fixture values are inconsistent")
    return {
        "status": status,
        "reason": None,
        "row_count": len(rows),
        "normalized_headers": headers,
        "player_links_valid": True,
    }


def _capture_split_result_bundle(request: CaptureRequest) -> dict:
    try:
        _validate_browserless_configuration()
    except Exception as exc:
        raise CaptureUnavailable(CAPTURE_FAILURE, stage="configuration") from exc
    try:
        manifest = _load_control_manifest(request)
        candidates = _representative_candidates(manifest)
    except Exception as exc:
        raise CaptureUnavailable(CAPTURE_FAILURE, stage="manifest") from exc
    urls = {}
    for stat_type, candidate in candidates.items():
        category_id = candidate.get("source_stat_category_id")
        if category_id:
            urls[stat_type] = build_ncaa_url(request.season_team_id, category_id)
    try:
        pages = _bounded_fetch(
            urls,
            request.limits,
            page_consumer=_interactive_split_consumer(candidates),
        ) if urls else {}
    except CaptureUnavailable:
        raise
    except Exception as exc:
        raise CaptureUnavailable(CAPTURE_FAILURE, stage="result_fetch") from exc
    captured_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    bundle = {}
    for stat_type in ("batting", "pitching"):
        try:
            content = _render_result_fixture(
                request=request,
                stat_type=stat_type,
                candidate=candidates[stat_type],
                raw_html=pages.get(stat_type),
                captured_at=captured_at,
            )
        except CaptureUnavailable as exc:
            if exc.stage:
                raise
            raise CaptureUnavailable(
                CAPTURE_FAILURE, stage=f"render_{stat_type}"
            ) from exc
        except Exception as exc:
            raise CaptureUnavailable(
                CAPTURE_FAILURE, stage=f"render_{stat_type}"
            ) from exc
        if len(content.encode("utf-8")) > request.limits.max_output_bytes:
            raise CaptureUnavailable(CAPTURE_FAILURE)
        bundle[f"{stat_type}_split_result.html"] = content
    return bundle


def capture_split_results(
    *,
    season_id: str,
    season_team_id: str,
    division: str,
    output_dir: str | Path,
    limits: CaptureLimits | None = None,
    write: bool = True,
) -> dict:
    request = validate_capture_request(
        season_id=season_id,
        season_team_id=season_team_id,
        division=division,
        output_dir=output_dir,
        limits=limits or CaptureLimits(),
    )
    bundle = _capture_split_result_bundle(request)
    if write:
        for filename, content in bundle.items():
            _atomic_write(request.output_dir / filename, content)
    return bundle


def _remote_command(args: argparse.Namespace) -> list[str]:
    command = ["python", "-", "--remote-worker"]
    if args.preflight:
        command.append("--preflight")
    elif args.controls_only:
        command.extend([
            "--season-id", args.season_id,
            "--season-team-id", args.season_team_id,
            "--division", args.division,
            "--output-dir", args.output_dir,
            "--controls-only",
            "--bundle-stdout",
        ])
    else:
        command.extend([
            "--season-id", args.season_id,
            "--season-team-id", args.season_team_id,
            "--division", args.division,
            "--output-dir", args.output_dir,
            "--capture-split-results",
            "--bundle-stdout",
        ])
    return command


def _run_on_existing_fly_runtime(args: argparse.Namespace) -> int:
    if args.preflight:
        # Validate the fixed default probe before opening the remote command.
        CaptureLimits(max_pages=1).validate()
    else:
        validate_capture_request(
            season_id=args.season_id,
            season_team_id=args.season_team_id,
            division=args.division,
            output_dir=args.output_dir,
            limits=CaptureLimits(),
        )
    try:
        source = Path(__file__).read_text(encoding="utf-8")
        roster_source = (PROJECT_ROOT / "roster_scraper.py").read_text(
            encoding="utf-8"
        )
        manifest_text = None
        if args.capture_split_results:
            request = validate_capture_request(
                season_id=args.season_id,
                season_team_id=args.season_team_id,
                division=args.division,
                output_dir=args.output_dir,
                limits=CaptureLimits(),
            )
            manifest_text = (request.output_dir / "fixture_manifest.json").read_text(
                encoding="utf-8"
            )
        loader = (
            "import os, sys, types\n"
            "module = types.ModuleType('roster_scraper')\n"
            "module.__file__ = '/app/roster_scraper.py'\n"
            "module.__package__ = ''\n"
            "sys.modules['roster_scraper'] = module\n"
            f"exec(compile({roster_source!r}, module.__file__, 'exec'), module.__dict__)\n"
            + (
                f"os.environ['PHASE3_CONTROL_MANIFEST_JSON'] = {manifest_text!r}\n"
                if manifest_text is not None else ""
            )
            + "script_globals = {'__name__': '__main__', "
              "'__file__': '/app/scripts/capture_phase3_ncaa_fixtures.py'}\n"
            f"exec(compile({source!r}, script_globals['__file__'], 'exec'), script_globals)\n"
        )
        command = " ".join(shlex.quote(part) for part in _remote_command(args))
        result = subprocess.run(
            ["fly", "ssh", "console", "-q", "-g", "app", "-C", command],
            cwd=PROJECT_ROOT,
            input=loader,
            text=True,
            capture_output=True,
            timeout=360,
            check=False,
        )
    except Exception:
        raise CaptureUnavailable(PREFLIGHT_FAILURE if args.preflight else CAPTURE_FAILURE)
    if result.returncode != 0:
        raise CaptureUnavailable(PREFLIGHT_FAILURE if args.preflight else CAPTURE_FAILURE)
    if args.preflight:
        if result.stdout.strip() != "phase3_browserless_ready":
            raise CaptureUnavailable(PREFLIGHT_FAILURE)
        print("phase3_browserless_ready")
        return 0
    try:
        bundle = json.loads(result.stdout)
        expected_files = (
            {"batting_split_result.html", "pitching_split_result.html"}
            if args.capture_split_results
            else {"fixture_manifest.json", "split_controls.html"}
        )
        if set(bundle) != expected_files:
            raise ValueError
        request = validate_capture_request(
            season_id=args.season_id,
            season_team_id=args.season_team_id,
            division=args.division,
            output_dir=args.output_dir,
            limits=CaptureLimits(),
        )
        for filename, content in bundle.items():
            if not isinstance(content, str) or len(content.encode("utf-8")) > request.limits.max_output_bytes:
                raise ValueError
            _atomic_write(request.output_dir / filename, content)
    except Exception:
        raise CaptureUnavailable(CAPTURE_FAILURE)
    return 0


def _parser() -> SafeArgumentParser:
    parser = SafeArgumentParser(add_help=True)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--season-id")
    parser.add_argument("--season-team-id")
    parser.add_argument("--division")
    parser.add_argument("--output-dir")
    parser.add_argument("--controls-only", action="store_true")
    parser.add_argument("--capture-split-results", action="store_true")
    parser.add_argument("--remote-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--bundle-stdout", action="store_true", help=argparse.SUPPRESS)
    return parser


def _should_use_fly(args: argparse.Namespace) -> bool:
    return (
        not args.remote_worker
        and not os.environ.get("BROWSERLESS_TOKEN")
        and os.environ.get("PHASE3_ALLOW_FLY_REMOTE", "1") == "1"
    )


def run(args: argparse.Namespace) -> int:
    if args.preflight:
        if any((args.season_id, args.season_team_id, args.division, args.output_dir)):
            raise ValueError("preflight does not accept capture arguments")
        if _should_use_fly(args):
            return _run_on_existing_fly_runtime(args)
        preflight()
        print("phase3_browserless_ready")
        return 0

    if args.controls_only and args.capture_split_results:
        raise ValueError("capture modes are mutually exclusive")
    if not args.controls_only and not args.capture_split_results:
        raise ValueError("capture mode is required")
    if not all((args.season_id, args.season_team_id, args.division, args.output_dir)):
        raise ValueError("capture identity is incomplete")
    if _should_use_fly(args):
        return _run_on_existing_fly_runtime(args)
    capture = capture_controls if args.controls_only else capture_split_results
    bundle = capture(
        season_id=args.season_id,
        season_team_id=args.season_team_id,
        division=args.division,
        output_dir=args.output_dir,
        write=not args.bundle_stdout,
    )
    if args.bundle_stdout:
        print(json.dumps(bundle, sort_keys=True, separators=(",", ":")))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        return run(args)
    except ValueError:
        print(INVALID_FAILURE, file=sys.stderr)
        return 2
    except CaptureUnavailable as exc:
        category = str(exc)
        if category not in {PREFLIGHT_FAILURE, CAPTURE_FAILURE}:
            category = CAPTURE_FAILURE
        print(category, file=sys.stderr)
        return 1
    except Exception:
        print(CAPTURE_FAILURE, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
