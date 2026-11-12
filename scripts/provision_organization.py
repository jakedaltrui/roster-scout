#!/usr/bin/env python3
"""Provision one organization and first-admin invitation as an operator."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise ValueError("organization provisioning invalid arguments")


def build_parser():
    parser = SafeArgumentParser(description="Provision a Roster Scout organization")
    parser.add_argument("--name", required=True)
    parser.add_argument("--admin-email", required=True)
    return parser


def _runtime_dependencies(environ):
    from roster_scout.database import create_session_factory, get_engine
    from roster_scout.identity import WorkOSIdentityProvider

    engine = get_engine(environ.get("DATABASE_URL"))
    provider = WorkOSIdentityProvider(
        api_key=environ.get("WORKOS_API_KEY", "").strip(),
        client_id=environ.get("WORKOS_CLIENT_ID", "").strip(),
        redirect_uri=environ.get("WORKOS_REDIRECT_URI", "").strip(),
        cookie_password=environ.get("WORKOS_COOKIE_PASSWORD", "").strip(),
    )
    return engine, create_session_factory(engine), provider


def main(
    argv=None,
    *,
    environ=None,
    stdout=None,
    stderr=None,
    session_factory=None,
    provider=None,
):
    environ = os.environ if environ is None else environ
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
        if not args.name.strip() or not args.admin_email.strip():
            raise ValueError("organization provisioning invalid arguments")
    except (ValueError, TypeError):
        print("organization provisioning invalid arguments", file=stderr)
        return 2

    engine = None
    try:
        if session_factory is None or provider is None:
            engine, session_factory, provider = _runtime_dependencies(environ)
        from roster_scout.organizations import (
            bootstrap_organization,
            deliver_invitation,
            mirror_organization,
        )

        with session_factory.begin() as session:
            organization, invitation = bootstrap_organization(
                session,
                name=args.name,
                admin_email=args.admin_email,
            )
            organization_id = organization.id
            invitation_id = invitation.id
        mirror_organization(
            session_factory,
            provider=provider,
            organization_id=organization_id,
        )
        deliver_invitation(
            session_factory,
            provider=provider,
            invitation_id=invitation_id,
        )
        print("organization provisioned", file=stdout)
        return 0
    except Exception:
        print("organization provisioning unavailable", file=stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
