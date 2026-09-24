from __future__ import annotations

import argparse
from collections.abc import Sequence

from hermes_factory import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-factory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(f"hermes-factory {__version__}")
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def entrypoint() -> None:
    raise SystemExit(main())
