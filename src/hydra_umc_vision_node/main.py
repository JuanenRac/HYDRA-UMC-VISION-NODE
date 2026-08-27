# =============================================================================
# HYDRA-UMC-VISION-NODE - entry point: src/hydra_umc_vision_node/main.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Entry point for HYDRA-UMC-VISION-NODE.

Bare invocation prints identity/version/role, unchanged from the
scaffolding stage. The real v0 work lives behind the `family-status`
subcommand: a real readiness check of this node's four real children,
reading each sibling's own hydra-umc.project.json - not the real
Hailo-8 runtime/gRPC control API/child-supervision logic the README's
own roadmap describes, which this integration parent was never meant to
run itself.
"""
from __future__ import annotations

import argparse
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .family import check_family_status

PROJECT_NAME = "HYDRA-UMC-VISION-NODE"
DIST_NAME = "hydra-umc-vision-node"
ROLE = (
    "High-speed perception edge AI node (Hailo-8 + CM5) - integration parent "
    "of Vision-Streamer, Detection-HEF, Safety-Zones and Visual-Servoing-API."
)

# This file lives at src/hydra_umc_vision_node/main.py - two parents up
# is this repo's own root, and one more level up is the workspace that
# holds it as a sibling of the other ecosystem repos.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_WORKSPACE = _REPO_ROOT.parent


def get_version() -> str:
    """Read the running version from installed package metadata, which is
    sourced from pyproject.toml - the single place bump_version.py edits.

    Why not a hardcoded __version__ string here instead? Because that would
    give this project two places to keep in sync on every build (pyproject.toml
    AND this module) instead of one. Reading it back from the installed
    package metadata means bump_version.py only ever has to touch
    pyproject.toml, and this function can never drift out of sync with it."""
    try:
        return version(DIST_NAME)
    except PackageNotFoundError:
        return "0.0.0-dev (package not installed - run build.sh/build.bat first)"


def _print_identity() -> None:
    print(f"{PROJECT_NAME} v{get_version()}")
    print(ROLE)


def _run_family_status(workspace: Path) -> int:
    statuses = check_family_status(workspace)
    print(f"Vision AI Node family status (workspace: {workspace}):")
    for status in statuses:
        if not status.present or status.manifest is None:
            print(f"  {status.name}: NOT FOUND")
            continue
        m = status.manifest
        print(f"  {status.name}: v{m.version}, maturity={m.maturity}, role={m.role}")

    missing = [status.name for status in statuses if not status.present]
    if missing:
        print(f"\n{len(missing)} of {len(statuses)} children not found: {', '.join(missing)}")
        return 1
    print(f"\nAll {len(statuses)} children present.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hydra-umc-vision-node", description=ROLE)
    subparsers = parser.add_subparsers(dest="command")

    family_parser = subparsers.add_parser(
        "family-status", help="Real readiness check of this node's four real children."
    )
    family_parser.add_argument(
        "--workspace",
        type=Path,
        default=_DEFAULT_WORKSPACE,
        help="Directory containing the sibling repo checkouts (default: this repo's own parent directory).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "family-status":
        return _run_family_status(args.workspace)

    _print_identity()
    return 0


if __name__ == "__main__":
    sys.exit(main())
