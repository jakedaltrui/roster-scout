#!/usr/bin/env python3
"""Print aggregate Roster Scout usage metrics for an operator-selected window.

Phase 1 anonymous workbook events count downloads but never unique users.
Phase 2 must record one ``authenticated_use`` only after an authorized
report/job request is durably accepted, keyed by its ReportJob UUID with the
canonical internal account and organization UUIDs. Page views do not qualify.
Email, IP, cookie, user-agent, fingerprint, and pseudonymous identifiers are
forbidden substitutes for the canonical account UUID.
"""

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class SafeArgumentParser(argparse.ArgumentParser):
    """Raise a fixed error instead of echoing operator input."""

    def error(self, _message):
        raise ValueError("usage report invalid arguments")


def build_parser():
    parser = SafeArgumentParser(
        description=(
            "Report aggregate successful workbook downloads and distinct "
            "authenticated users. Phase 1 anonymous downloads never count "
            "as users. Phase 2 records authenticated_use only after an "
            "authorized report/job request is accepted, keyed by the accepted "
            "ReportJob UUID with canonical account and organization UUIDs; "
            "page views do not qualify. Email, IP, cookie, user-agent, "
            "fingerprint, and pseudonymous IDs are forbidden substitutes."
        )
    )
    parser.add_argument(
        "--from",
        dest="window_start",
        required=True,
        help="inclusive ISO-8601 UTC window start",
    )
    parser.add_argument(
        "--to",
        dest="window_end",
        required=True,
        help="exclusive ISO-8601 UTC window end",
    )
    parser.add_argument(
        "--organization-id",
        help="optional canonical organization UUID",
    )
    return parser


def _parse_utc(value):
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("usage report invalid arguments")
    return parsed.astimezone(UTC)


def _runtime_dependencies(database_url=None):
    """Construct database dependencies only after arguments validate."""
    from roster_scout.database import create_session_factory, get_engine

    engine = get_engine(database_url)
    return engine, create_session_factory(engine)


def _iso_utc(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def main(argv=None, *, environ=None, stdout=None, stderr=None):
    environ = os.environ if environ is None else environ
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
        start = _parse_utc(args.window_start)
        end = _parse_utc(args.window_end)
        if start >= end:
            raise ValueError("usage report invalid arguments")
        organization_id = (
            uuid.UUID(args.organization_id)
            if args.organization_id is not None
            else None
        )
    except (ValueError, TypeError):
        print("usage report invalid arguments", file=stderr)
        return 2

    engine = None
    try:
        engine, session_factory = _runtime_dependencies(environ.get("DATABASE_URL"))
        from roster_scout.usage import query_usage_metrics

        with session_factory() as session:
            metrics = query_usage_metrics(
                session,
                start=start,
                end=end,
                organization_id=organization_id,
            )
        payload = {
            "window_start": _iso_utc(metrics.window_start),
            "window_end": _iso_utc(metrics.window_end),
            "unique_authenticated_users": metrics.unique_authenticated_users,
            "successful_workbook_downloads": (
                metrics.successful_workbook_downloads
            ),
        }
        if metrics.organization_id is not None:
            payload["organization_id"] = str(metrics.organization_id)
        print(json.dumps(payload, sort_keys=True), file=stdout)
        return 0
    except Exception:
        print("usage report unavailable", file=stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
