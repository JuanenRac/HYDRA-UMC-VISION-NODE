# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/api.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Plain JSON/HTTP surface (stdlib http.server) - same convention as this
family's other api.py files. All three routes below reach the exact
functions main.py's own `family-status`/`pipeline-status`/`validate-frame`
subcommands already run. POST /validate-frame takes the raw frame bytes
as the request body rather than a server-side file path: main.py's own
`validate-frame` subcommand reads a *local* file because it is a CLI
running on the same machine as the frame, but a real remote caller has no
such local path on this server's own filesystem to hand it - the bytes
themselves are the only thing that travels over HTTP.
"""
from __future__ import annotations

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import json

from .family import check_family_status
from .frame import FrameSpec, validate_frame
from .hardware import accelerator_available, active_stages, camera_available, determine_mode
from .pipeline import build_manifest, manifest_to_dict


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    def default(o: object) -> object:
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)
        if hasattr(o, "value"):  # enum
            return o.value
        return str(o)
    body = json.dumps(payload, default=default).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_error(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
    _write_json(handler, status, {"error": message})


def _query_params(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    parsed = urlparse(handler.path)
    values = parse_qs(parsed.query, keep_blank_values=True)
    repeated = sorted(key for key, value in values.items() if len(value) != 1)
    if repeated:
        raise ValueError(f"query parameters must occur exactly once: {repeated}")
    return {key: value[0] for key, value in values.items()}


class Handler(BaseHTTPRequestHandler):
    server: "VisionNodeServer"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # quiet by default, same reasoning as this family's other api.py files

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            params = _query_params(self)
        except ValueError as error:
            _write_error(self, 400, str(error))
            return

        if path == "/family-status":
            self._handle_family_status(params)
        elif path == "/pipeline-status":
            self._handle_pipeline_status()
        elif path == "/stats":
            _write_json(self, 200, {"workspace": str(self.server.workspace)})
        else:
            _write_error(self, 404, "not found")

    def _handle_family_status(self, params: dict[str, str]) -> None:
        workspace = Path(params["workspace"]) if "workspace" in params else self.server.workspace
        statuses = check_family_status(workspace)
        missing = [s.name for s in statuses if not s.present]
        _write_json(self, 200, {
            "workspace": str(workspace),
            "children": [asdict(s) for s in statuses],
            "missing": missing,
            "allPresent": not missing,
        })

    def _handle_pipeline_status(self) -> None:
        manifest = build_manifest()
        camera = camera_available()
        accelerator = accelerator_available()
        mode = determine_mode(camera, accelerator)
        runnable, skipped = active_stages(manifest, mode)
        _write_json(self, 200, {
            "manifest": manifest_to_dict(manifest),
            "cameraPresent": camera,
            "acceleratorPresent": accelerator,
            "mode": mode.value,
            "runnableStages": [s.name for s in runnable],
            "skippedStages": [s.name for s in skipped],
        })

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            params = _query_params(self)
        except ValueError as error:
            _write_error(self, 400, str(error))
            return
        if path == "/validate-frame":
            self._handle_validate_frame(params)
        else:
            _write_error(self, 404, "not found")

    def _handle_validate_frame(self, params: dict[str, str]) -> None:
        missing = {"width", "height"} - params.keys()
        if missing:
            _write_error(self, 400, f"missing required params: {sorted(missing)}")
            return
        try:
            width = int(params["width"])
            height = int(params["height"])
            channels = int(params.get("channels", "3"))
        except ValueError as e:
            _write_error(self, 400, f"width/height/channels must be integers: {e}")
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        data = self.rfile.read(length) if length > 0 else b""

        # FrameSpec itself never raises (frozen dataclass, no __post_init__
        # validation) - a non-positive dimension is reported as a real
        # FrameValidationIssue by validate_frame() below, the same path
        # main.py's own validate-frame subcommand already relies on.
        spec = FrameSpec(width=width, height=height, channels=channels)
        issues = validate_frame(data, spec)
        _write_json(self, 200, {
            "valid": not issues,
            "expectedBytes": spec.expected_bytes,
            "actualBytes": len(data),
            "issues": [asdict(i) for i in issues],
        })


class VisionNodeServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], workspace: Path) -> None:
        super().__init__(address, Handler)
        self.workspace = workspace
