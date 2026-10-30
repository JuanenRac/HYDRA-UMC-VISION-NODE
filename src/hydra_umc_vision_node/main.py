# =============================================================================
# HYDRA-UMC-VISION-NODE - entry point: src/hydra_umc_vision_node/main.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Entry point for HYDRA-UMC-VISION-NODE.

Bare invocation prints identity/version/role, unchanged from the
scaffolding stage. The real v0 work lives behind three subcommands:
`family-status` (a real readiness check of this node's four real
children, reading each sibling's own hydra-umc.project.json),
`pipeline-status` (the real, inspectable shape of this node's
perception pipeline plus real degraded-mode detection - which stages
can actually run given whatever camera/accelerator hardware is really
present), and `validate-frame` (real structural corruption checking of
a raw frame buffer). None of this is the real Hailo-8 runtime/gRPC
control API/child-supervision logic the README's own roadmap describes,
which this integration parent was never meant to run itself and which
needs real CM5+Hailo-8 hardware this environment does not have.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .access import bind_problem, load_token
from .api import VisionNodeServer
from .family import check_family_status
from .live import DEFAULT_TIMEOUT_SECONDS, check_family_health
from .frame import FrameSpec, validate_frame
from .hardware import PipelineMode, accelerator_available, active_stages, camera_available, determine_mode
from .pipeline import build_manifest, pipeline_status_dict

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
# An installed (non-editable) package lives in site-packages, where "two parents
# up" is meaningless, so HYDRA_UMC_WORKSPACE (set by the container image) wins.
_DEFAULT_WORKSPACE = Path(os.environ.get("HYDRA_UMC_WORKSPACE") or _REPO_ROOT.parent)


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


def _run_family_status(workspace: Path, live: bool = False, host: str = "127.0.0.1", timeout: float = DEFAULT_TIMEOUT_SECONDS) -> int:
    statuses = check_family_status(workspace)
    print(f"Vision AI Node family status (workspace: {workspace}):")
    for status in statuses:
        if not status.present or status.manifest is None:
            print(f"  {status.name}: NOT FOUND")
            continue
        m = status.manifest
        print(f"  {status.name}: v{m.version}, maturity={m.maturity}, role={m.role}")

    if live:
        print(f"\nLive health (host {host}):")
        down = 0
        for health in check_family_health(statuses, host, timeout):
            if health.state == "up":
                print(f"  {health.name}: up, {health.latency_ms} ms, version {health.served_version or 'not reported'}")
            elif health.state == "no_service":
                print(f"  {health.name}: {health.detail}")
            else:
                down += 1
                print(f"  {health.name}: DOWN ({health.detail})")
        if down:
            print(f"\n{down} service(s) did not answer.")
            return 1

    missing = [status.name for status in statuses if not status.present]
    if missing:
        print(f"\n{len(missing)} of {len(statuses)} children not found: {', '.join(missing)}")
        return 1
    print(f"\nAll {len(statuses)} children present.")
    return 0


def _run_pipeline_status() -> int:
    manifest = build_manifest()
    camera = camera_available()
    accelerator = accelerator_available()
    mode = determine_mode(camera, accelerator)
    runnable, skipped = active_stages(manifest, mode)

    output = pipeline_status_dict(
        manifest, camera, accelerator, mode.value, [s.name for s in runnable], [s.name for s in skipped]
    )
    print(json.dumps(output, indent=2))

    return 0 if mode == PipelineMode.FULL else 1


def _run_validate_frame(path: Path, width: int, height: int, channels: int) -> int:
    try:
        data = path.read_bytes()
    except OSError as e:
        print(f"[vision-node] could not read {path}: {e}")
        return 2

    spec = FrameSpec(width=width, height=height, channels=channels)
    issues = validate_frame(data, spec)

    if not issues:
        print(f"Frame OK: {path} matches {width}x{height}x{channels} ({spec.expected_bytes} bytes)")
        return 0

    print(f"Frame INVALID: {path}")
    for issue in issues:
        print(f"  [{issue.kind}] {issue.detail}")
    return 1


def _run_serve(addr: str, port: int, workspace: Path, token_file: str | None = None) -> int:
    try:
        token = load_token(token_file)
    except ValueError as error:
        print(f"[vision-node] error: {error}", file=sys.stderr)
        return 2
    problem = bind_problem(addr, token)
    if problem:
        print(f"[vision-node] error: {problem}", file=sys.stderr)
        return 2
    if not workspace.is_dir():
        print(f"[vision-node] warning: workspace {workspace} is not a directory - every child will report as missing", file=sys.stderr)
    server = VisionNodeServer((addr, port), workspace, token)
    print(f"[vision-node] HTTP API listening on {addr}:{port} (workspace={workspace}; {'Bearer token required' if token else 'no token, loopback only'})")
    print("[vision-node] GET /family-status, GET /pipeline-status, POST /validate-frame, GET /stats")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("[vision-node] shutting down")
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

    family_parser.add_argument(
        "--live",
        action="store_true",
        help="Also ask each child that declares a service for its health (one short GET on the port its manifest names); off by default.",
    )
    family_parser.add_argument("--host", default="127.0.0.1", help="Host the children listen on for --live (default: loopback).")
    family_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="Seconds to wait for each child with --live.")

    subparsers.add_parser(
        "pipeline-status",
        help="Real pipeline manifest plus real degraded-mode detection for this node's actual hardware.",
    )

    validate_frame_parser = subparsers.add_parser(
        "validate-frame", help="Real structural corruption check of a raw frame buffer file."
    )
    validate_frame_parser.add_argument("path", type=Path, help="Path to a raw frame buffer file.")
    validate_frame_parser.add_argument("--width", type=int, required=True)
    validate_frame_parser.add_argument("--height", type=int, required=True)
    validate_frame_parser.add_argument("--channels", type=int, default=3, help="Default: 3 (RGB).")

    serve_parser = subparsers.add_parser(
        "serve",
        help="Run family-status/pipeline-status/validate-frame as a JSON/HTTP API "
             "(GET /family-status, GET /pipeline-status, POST /validate-frame) - the "
             "exact same functions the CLI subcommands above already run.",
    )
    serve_parser.add_argument("--addr", default="127.0.0.1", help="address to bind the HTTP API to")
    serve_parser.add_argument("--port", type=int, default=8094, help="port for the HTTP API")
    serve_parser.add_argument("--token-file", default=None, help="file with the bearer token every request must present (or the HYDRA_UMC_VISION_NODE_TOKEN environment variable). Needed to bind an address that is not loopback.")
    serve_parser.add_argument(
        "--workspace",
        type=Path,
        default=_DEFAULT_WORKSPACE,
        help="Default workspace for GET /family-status when it is not overridden per-request.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "family-status":
        return _run_family_status(args.workspace, args.live, args.host, args.timeout)
    if args.command == "pipeline-status":
        return _run_pipeline_status()
    if args.command == "validate-frame":
        return _run_validate_frame(args.path, args.width, args.height, args.channels)
    if args.command == "serve":
        return _run_serve(args.addr, args.port, args.workspace, args.token_file)

    _print_identity()
    return 0


if __name__ == "__main__":
    sys.exit(main())
