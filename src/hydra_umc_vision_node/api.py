# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/api.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
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

from .access import authorized, reject
from .codes import DecoderUnavailable, decode_codes, pylibdmtx_decoder
from .family import check_family_status
from .frame import MAX_CHANNELS, MAX_DIMENSION, MAX_FRAME_BYTES, FrameSpec, validate_frame
from .hardware import accelerator_available, active_stages, camera_available, determine_mode
from .latency import LatencyError, LatencyRecorder
from .pipeline import build_manifest, pipeline_status_dict


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


# Largest body of POST /latency/record (a few stage instants)
LATENCY_BODY_BYTES = 4096


def _reject_constant(name: str) -> float:
    raise ValueError(f"{name} is not a valid JSON number")


# GET path the project manifest declares as the health probe; it carries no token
HEALTH_PATH = "/stats"


class Handler(BaseHTTPRequestHandler):
    server: "VisionNodeServer"

    # Seconds a client may stay silent on its connection before the server
    # drops it, so a client that connects and sends nothing cannot hold a
    # handler thread forever. Socket I/O only; never the time a check takes.
    timeout = 15.0

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # quiet by default, same reasoning as this family's other api.py files

    def do_GET(self) -> None:  # noqa: N802
        # the health path of the manifest stays open so the service registry can probe the service without the token
        if urlparse(self.path).path != HEALTH_PATH and not authorized(self, self.server.token):
            reject(self)
            return
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
        elif path == "/latency" and self.server.latency is not None:
            _write_json(self, 200, self.server.latency.snapshot())
        elif path == "/stats":
            _write_json(self, 200, {"workspace": str(self.server.workspace)})
        else:
            _write_error(self, 404, "not found")

    def _handle_family_status(self, params: dict[str, str]) -> None:
        workspace = self.server.workspace
        if "workspace" in params:
            # A client may only look at a folder inside the workspace the
            # server was started with, never at an arbitrary path.
            requested = Path(params["workspace"])
            try:
                inside = requested.resolve().is_relative_to(self.server.workspace.resolve())
            except OSError:
                inside = False
            if not inside:
                _write_error(self, 400, "workspace must be inside the workspace this server was started with")
                return
            workspace = requested
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
        _write_json(self, 200, pipeline_status_dict(
            manifest, camera, accelerator, mode.value, [s.name for s in runnable], [s.name for s in skipped]
        ))

    def do_POST(self) -> None:  # noqa: N802
        if not authorized(self, self.server.token):
            reject(self)
            return
        path = urlparse(self.path).path
        try:
            params = _query_params(self)
        except ValueError as error:
            _write_error(self, 400, str(error))
            return
        if path == "/validate-frame":
            self._handle_validate_frame(params)
        elif path == "/read-code":
            self._handle_read_code(params)
        elif path == "/latency/record" and self.server.latency is not None:
            self._handle_latency_record()
        else:
            _write_error(self, 404, "not found")

    def _handle_latency_record(self) -> None:
        """Record the instants one frame passed through the stages: {"camera": name, "stamps": {stage: seconds}}."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > LATENCY_BODY_BYTES:
                raise ValueError(f"request body must contain 0-{LATENCY_BODY_BYTES} bytes")
            body = json.loads(self.rfile.read(length) if length else b"{}", parse_constant=_reject_constant)
            if not isinstance(body, dict):
                raise ValueError("the body must be a JSON object")
            durations = self.server.latency.record(body.get("camera"), body.get("stamps"))  # type: ignore[union-attr]
        except (ValueError, LatencyError) as error:
            _write_error(self, 400, str(error))
            return
        _write_json(self, 200, {"recorded": True, "totalMs": durations["total"]})

    def _handle_read_code(self, params: dict[str, str]) -> None:
        """Read the DataMatrix codes of one raw frame: the frame travels as the body, exactly as for /validate-frame."""
        frame = self._read_frame(params)
        if frame is None:
            return
        spec, data = frame
        issues = [issue for issue in validate_frame(data, spec) if issue.kind != "uniform_buffer"]
        if issues:
            _write_json(self, 400, {"error": "the frame is not valid", "issues": [asdict(i) for i in issues]})
            return
        try:
            codes = decode_codes(data, spec, self.server.code_decoder)
        except DecoderUnavailable as error:
            _write_error(self, 503, str(error))
            return
        _write_json(self, 200, {"codes": codes, "count": len(codes)})

    def _handle_validate_frame(self, params: dict[str, str]) -> None:
        frame = self._read_frame(params)
        if frame is None:
            return
        spec, data = frame
        issues = validate_frame(data, spec)
        _write_json(self, 200, {
            "valid": not issues,
            "expectedBytes": spec.expected_bytes,
            "actualBytes": len(data),
            "issues": [asdict(i) for i in issues],
        })

    def _read_frame(self, params: dict[str, str]) -> tuple[FrameSpec, bytes] | None:
        """The frame spec of the query and the body of the request, or None after the error has been answered."""
        missing = {"width", "height"} - params.keys()
        if missing:
            _write_error(self, 400, f"missing required params: {sorted(missing)}")
            return None
        try:
            width = int(params["width"])
            height = int(params["height"])
            channels = int(params.get("channels", "3"))
        except ValueError as e:
            _write_error(self, 400, f"width/height/channels must be integers: {e}")
            return None

        # FrameSpec itself never raises (frozen dataclass, no __post_init__
        # validation) - a non-positive dimension is reported as a real
        # FrameValidationIssue by validate_frame() below, the same path
        # main.py's own validate-frame subcommand already relies on.
        spec = FrameSpec(width=width, height=height, channels=channels)

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError as e:
            _write_error(self, 400, f"Content-Length must be an integer: {e}")
            return None
        # This used
        # to read a caller-controlled Content-Length with no upper bound at
        # all before this fix - a lying/oversized header could force
        # unbounded memory buffering. Unlike a JSON endpoint there is no one
        # fixed byte cap that makes sense here (an honest frame's size
        # varies with resolution) - the request's own declared width/
        # height/channels already say exactly how large it should ever
        # need to be, so that is the bound. One extra byte of slack is
        # enough to still report an oversized frame as `size_mismatch`
        # below instead of silently truncating it.
        # A spec that is non-positive or beyond the node's limits has no
        # honest body at all: refuse it before reading anything (a negative
        # width times a negative height used to give a huge positive size).
        spec_is_sane = (
            0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION and 0 < channels <= MAX_CHANNELS
        )
        if not spec_is_sane:
            _write_error(
                self, 400,
                f"width and height must be between 1 and {MAX_DIMENSION} and channels between 1 and {MAX_CHANNELS}",
            )
            return None
        max_acceptable = min(spec.expected_bytes + 1, MAX_FRAME_BYTES)
        if length < 0 or length > max_acceptable:
            # Same reasoning as HYDRA-UMC-ANOMALY-DETECTOR's own body cap:
            # draining a bounded amount of an over-limit request before
            # responding avoids a real ConnectionAbortedError race where
            # the client's own send() is still in flight when this handler
            # closes the connection.
            drain_cap = max_acceptable * 4
            if 0 <= length <= drain_cap:
                _read_exactly(self.rfile, length)
            _write_error(
                self, 400,
                f"request body must contain at most {max_acceptable} bytes "
                f"for a {width}x{height}x{channels} frame",
            )
            return None
        data = _read_exactly(self.rfile, length) if length > 0 else b""
        return spec, data


def _read_exactly(rfile, length: int, chunk: int = 1 << 20) -> bytes:
    """Reads `length` bytes in bounded chunks (stops early if the client closes)."""
    parts: list[bytes] = []
    remaining = length
    while remaining > 0:
        block = rfile.read(min(chunk, remaining))
        if not block:
            break
        parts.append(block)
        remaining -= len(block)
    return b"".join(parts)


class VisionNodeServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], workspace: Path, token: str = "", code_decoder=pylibdmtx_decoder, latency: LatencyRecorder | None = None) -> None:
        super().__init__(address, Handler)
        # per-stage latency percentiles (latency.py); None (the default) leaves GET /latency and POST /latency/record answering 404
        self.latency = latency
        # reads the DataMatrix codes of a frame (codes.py); replaceable, so the route is tested without the library
        self.code_decoder = code_decoder
        # empty means no token (loopback only; see access.py)
        self.token = token
        self.workspace = workspace
