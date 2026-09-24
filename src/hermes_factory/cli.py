from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from hermes_factory import __version__
from hermes_factory.errors import FactoryError
from hermes_factory.hermes_cli import HermesClient
from hermes_factory.models import RigSpec
from hermes_factory.rig_loader import load_rig

ClientFactory = Callable[[str], HermesClient]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-factory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--rig", type=Path, required=True)
    validate.add_argument("--hermes", default="hermes")
    return parser


def validate_rig_profiles(rig: RigSpec, client: HermesClient) -> tuple[str, ...]:
    profiles = dict.fromkeys(seat.profile for seat in rig.seats.values())
    return tuple(profile for profile in profiles if not client.profile_exists(profile))


def warn_nonisolated_seats(rig: RigSpec) -> None:
    seats_by_profile: dict[str, list[str]] = {}
    for seat_name, seat in rig.seats.items():
        seats_by_profile.setdefault(seat.profile, []).append(seat_name)
    for profile, seat_names in seats_by_profile.items():
        if len(seat_names) > 1:
            print(
                f"warning: seats {', '.join(seat_names)} share Hermes profile "
                f"{profile!r}; these seats are not isolated",
                file=sys.stderr,
            )


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: ClientFactory = HermesClient,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(f"hermes-factory {__version__}")
        return 0
    if args.command == "validate":
        try:
            rig = load_rig(args.rig)
            missing = validate_rig_profiles(rig, client_factory(args.hermes))
        except FactoryError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if missing:
            print(f"error: missing Hermes profiles: {', '.join(missing)}", file=sys.stderr)
            return 2
        warn_nonisolated_seats(rig)
        print(f"valid: {rig.project.slug} ({len(rig.seats)} seats)")
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def entrypoint() -> None:
    raise SystemExit(main())
