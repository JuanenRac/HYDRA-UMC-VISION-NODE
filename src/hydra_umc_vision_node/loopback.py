# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/loopback.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The reference pipeline "loopback": capture, detect and publish without a Hailo accelerator, in the data format of the pipeline contract.

The frames come from a configured source of the simulation (a synthetic generator or a folder of PGM/PPM images) or from the MJPEG stream of
VISION-STREAMER (`GET /stream`, whose parts carry `X-Frame-Id`, `X-Session-Id` and `X-Capture-Ts`). The "model" is a trivial detector that runs on the
CPU with the standard library: the frame is cut into square cells, a cell is bright when its brightest pixel reaches the threshold, and every group of
touching bright cells is one detection (its box is the box of the group, its score the share of the box that is bright). It is not a neural network and
claims no precision: it exists so that the whole path capture -> detections -> publish runs today and produces `FrameMeta` and `DetectionFrame`
messages with the identity of the detector attached, which is what SAFETY-ZONES and VISUAL-SERVOING need to be tried with data that has the real shape.
When a compiled model exists, a detector that runs it takes the place of this one and the rest does not change.

Turning a JPEG into pixels needs a library; Pillow is imported on first use and, without it, reading the stream answers that it is not installed.
Everything is bounded: bytes per frame, frames per run, seconds per run, detections per frame.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from .compat_record import CompatibilityRecord
from .contract import ContractError, build_detection_frame, build_frame_meta, iso_utc
from .latency import LatencyError, LatencyRecorder
from .netguard import EndpointError, parse_endpoint
from .pipeline import PIPELINE_VERSION
from .sources import MAX_SOURCE_FRAME_BYTES, Frame, FrameSource, SourceError

MAX_RUN_FRAMES = 100
MAX_DETECTIONS = 64
MAX_CELL = 64
DETECTOR_NAME = "loopback-threshold"
DETECTOR_VERSION = "1"
DETECTOR_ARCH = "cpu"
DEFAULT_STREAM_SECONDS = 5.0
MAX_STREAM_SECONDS = 60.0
_LABEL = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_KEYS = {"enabled", "detector", "streamer"}
_DETECTOR_KEYS = {"threshold", "cell", "minCells", "maxDetections", "label"}
_STREAMER_KEYS = {"url", "allowRemote", "timeoutSeconds", "maxFrameBytes"}


class LoopbackError(ValueError):
    """The configuration of the loopback pipeline or a run of it cannot be used."""


class StreamUnavailable(RuntimeError):
    """The MJPEG stream cannot be read (not reachable, not a multipart stream, no decoder)."""


@dataclass(frozen=True)
class DetectorSettings:
    threshold: int = 200
    cell: int = 8
    min_cells: int = 1
    max_detections: int = 16
    label: str = "bright"

    def describe(self) -> dict:
        return {"threshold": self.threshold, "cell": self.cell, "minCells": self.min_cells, "maxDetections": self.max_detections, "label": self.label}

    def identity(self) -> CompatibilityRecord:
        """The identity the detections carry: the settings are part of what produced them, so they are part of the hash."""
        canonical = json.dumps(self.describe(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(f"{DETECTOR_NAME}:{DETECTOR_VERSION}:{canonical}".encode()).hexdigest()
        return CompatibilityRecord(DETECTOR_NAME, DETECTOR_VERSION, digest, DETECTOR_ARCH, PIPELINE_VERSION)


@dataclass(frozen=True)
class StreamerSettings:
    host: str
    port: int
    path: str
    timeout_seconds: float
    max_frame_bytes: int

    def describe(self) -> dict:
        return {"url": f"http://{self.host}:{self.port}{self.path}", "timeoutSeconds": self.timeout_seconds, "maxFrameBytes": self.max_frame_bytes}


@dataclass(frozen=True)
class LoopbackConfig:
    enabled: bool
    detector: DetectorSettings
    streamer: StreamerSettings | None

    def describe(self) -> dict:
        return {"enabled": self.enabled, "detector": self.detector.describe(), "streamer": self.streamer.describe() if self.streamer else None,
                "model": self.detector.identity().to_dict()}


def _bounded_int(block: dict, key: str, default: int, low: int, high: int) -> int:
    value = block.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise LoopbackError(f"{key} must be an integer from {low} to {high}")
    return value


def parse_loopback_config(config: object) -> LoopbackConfig | None:
    """The optional `loopback` block. None when absent; `enabled` is false unless the block says true."""
    if config is None:
        return None
    if not isinstance(config, dict) or set(config) - _KEYS:
        raise LoopbackError(f"loopback is an object with the keys {sorted(_KEYS)}")
    enabled = config.get("enabled", False)
    if not isinstance(enabled, bool):
        raise LoopbackError("loopback.enabled must be true or false")
    block = config.get("detector", {})
    if not isinstance(block, dict) or set(block) - _DETECTOR_KEYS:
        raise LoopbackError(f"loopback.detector is an object with the keys {sorted(_DETECTOR_KEYS)}")
    label = block.get("label", "bright")
    if not isinstance(label, str) or not _LABEL.fullmatch(label):
        raise LoopbackError("loopback.detector.label is 1 to 64 characters of letters, digits and _ . : -")
    detector = DetectorSettings(
        threshold=_bounded_int(block, "threshold", 200, 1, 255), cell=_bounded_int(block, "cell", 8, 1, MAX_CELL),
        min_cells=_bounded_int(block, "minCells", 1, 1, 4096), max_detections=_bounded_int(block, "maxDetections", 16, 1, MAX_DETECTIONS), label=label,
    )
    streamer = None
    if "streamer" in config:
        raw = config["streamer"]
        if not isinstance(raw, dict) or set(raw) - _STREAMER_KEYS:
            raise LoopbackError(f"loopback.streamer is an object with the keys {sorted(_STREAMER_KEYS)}")
        allow_remote = raw.get("allowRemote", False)
        if not isinstance(allow_remote, bool):
            raise LoopbackError("loopback.streamer.allowRemote must be true or false")
        try:
            endpoint = parse_endpoint(raw.get("url"), allow_remote, "loopback.streamer.url")
        except EndpointError as error:
            raise LoopbackError(str(error)) from None
        seconds = raw.get("timeoutSeconds", DEFAULT_STREAM_SECONDS)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 < seconds <= MAX_STREAM_SECONDS:
            raise LoopbackError(f"loopback.streamer.timeoutSeconds must be a number above 0 and at most {MAX_STREAM_SECONDS:g}")
        streamer = StreamerSettings(endpoint.host, endpoint.port, endpoint.path, float(seconds),
                                    _bounded_int(raw, "maxFrameBytes", MAX_SOURCE_FRAME_BYTES, 1024, MAX_SOURCE_FRAME_BYTES))
    return LoopbackConfig(enabled, detector, streamer)


# ---------------------------------------------------------------------------------------------------------------- detector


def _brightness(frame: Frame) -> bytes | bytearray:
    """One brightness byte per pixel: the byte itself for one channel, the mean of the first three for more."""
    data, channels = frame.data, frame.channels
    if channels == 1:
        return data
    if channels == 2:
        return data[0::2]
    r, g, b = data[0::channels], data[1::channels], data[2::channels]
    return bytes((x + y + z) // 3 for x, y, z in zip(r, g, b, strict=False))


def detect(frame: Frame, settings: DetectorSettings) -> list[dict[str, Any]]:
    """The detections of one frame, boxes in pixels, strongest first (largest group, then top left). Deterministic."""
    width, height, cell = frame.width, frame.height, settings.cell
    if len(frame.data) != width * height * frame.channels:
        raise LoopbackError("the frame does not hold the bytes its size announces")
    gray = _brightness(frame)
    cols, rows = -(-width // cell), -(-height // cell)
    bright = bytearray(cols * rows)
    for y in range(height):
        line = gray[y * width:(y + 1) * width]
        base = (y // cell) * cols
        for cx in range(cols):
            if bright[base + cx] == 0 and max(line[cx * cell:(cx + 1) * cell]) >= settings.threshold:
                bright[base + cx] = 1
    seen = bytearray(cols * rows)
    groups: list[tuple[int, int, int, int, int]] = []  # cells, min col, min row, max col, max row
    for start in range(cols * rows):
        if not bright[start] or seen[start]:
            continue
        seen[start] = 1
        stack = [start]
        count, c0, r0, c1, r1 = 0, cols, rows, -1, -1
        while stack:
            index = stack.pop()
            col, row = index % cols, index // cols
            count += 1
            c0, r0, c1, r1 = min(c0, col), min(r0, row), max(c1, col), max(r1, row)
            for ncol, nrow in ((col - 1, row), (col + 1, row), (col, row - 1), (col, row + 1)):
                if 0 <= ncol < cols and 0 <= nrow < rows:
                    neighbour = nrow * cols + ncol
                    if bright[neighbour] and not seen[neighbour]:
                        seen[neighbour] = 1
                        stack.append(neighbour)
        if count >= settings.min_cells:
            groups.append((count, c0, r0, c1, r1))
    groups.sort(key=lambda g: (-g[0], g[2], g[1]))
    detections = []
    for count, c0, r0, c1, r1 in groups[:settings.max_detections]:
        box = {"x_min": float(c0 * cell), "y_min": float(r0 * cell), "x_max": float(min(width, (c1 + 1) * cell)), "y_max": float(min(height, (r1 + 1) * cell))}
        area_cells = (c1 - c0 + 1) * (r1 - r0 + 1)
        detections.append({"class_id": 0, "label": settings.label, "score": round(count / area_cells, 4), "box": box})
    return detections


# ------------------------------------------------------------------------------------------------------------------ stream


@dataclass(frozen=True)
class StreamPart:
    jpeg: bytes
    frame_id: str | None
    session_id: str | None
    capture_ts_ms: int | None


Decoder = Callable[[bytes], Frame]


def pillow_decoder(jpeg: bytes) -> Frame:
    """JPEG to an RGB frame with Pillow, imported on first use."""
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        raise StreamUnavailable("Pillow is not installed: install it (python3-pil) to read the JPEG frames of the stream") from None
    import io  # noqa: PLC0415

    try:
        with Image.open(io.BytesIO(jpeg)) as image:
            image.load()
            rgb = image.convert("RGB")
            width, height = rgb.size
            if width * height * 3 > MAX_SOURCE_FRAME_BYTES:
                raise StreamUnavailable(f"a frame may hold at most {MAX_SOURCE_FRAME_BYTES} bytes")
            return Frame(0, width, height, 3, rgb.tobytes())
    except StreamUnavailable:
        raise
    except Exception as error:  # noqa: BLE001 - any decoder failure is a bad frame, not a crash
        raise StreamUnavailable(f"a frame of the stream cannot be decoded: {type(error).__name__}") from None


def _boundary(content_type: str) -> str:
    match = re.search(r'boundary="?([^";\s]+)"?', content_type, re.IGNORECASE)
    if not match or not content_type.lower().startswith("multipart/x-mixed-replace"):
        raise StreamUnavailable("the stream is not multipart/x-mixed-replace")
    return match.group(1)


def read_stream(settings: StreamerSettings, limit: int, clock: Callable[[], float] = time.monotonic) -> Iterator[StreamPart]:
    """At most `limit` parts of the MJPEG stream, within `settings.timeout_seconds` in total."""
    deadline = clock() + settings.timeout_seconds
    connection = http.client.HTTPConnection(settings.host, settings.port, timeout=settings.timeout_seconds)
    try:
        try:
            connection.request("GET", settings.path, headers={"Accept": "multipart/x-mixed-replace"})
            response = connection.getresponse()
        except (OSError, http.client.HTTPException) as error:
            raise StreamUnavailable(f"the stream cannot be reached: {type(error).__name__}") from None
        if response.status != 200:
            raise StreamUnavailable(f"the stream answered HTTP {response.status}")
        marker = ("--" + _boundary(response.getheader("Content-Type", ""))).encode("ascii")
        produced = 0
        while produced < limit and clock() < deadline:
            try:
                line = response.fp.readline(1024)  # type: ignore[union-attr]
            except (OSError, http.client.HTTPException):
                break
            if not line:
                break
            if not line.strip().startswith(marker):
                continue
            headers: dict[str, str] = {}
            for _ in range(32):
                raw = response.fp.readline(1024)  # type: ignore[union-attr]
                if raw in (b"\r\n", b"\n", b""):
                    break
                name, _, value = raw.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            try:
                size = int(headers.get("content-length", ""))
            except ValueError:
                raise StreamUnavailable("a part of the stream has no Content-Length") from None
            if not 0 < size <= settings.max_frame_bytes:
                raise StreamUnavailable(f"a part of the stream holds {size} bytes: at most {settings.max_frame_bytes} are read")
            jpeg = response.read(size)
            if len(jpeg) != size:
                break
            stamp = headers.get("x-capture-ts", "")
            yield StreamPart(jpeg, headers.get("x-frame-id") or None, headers.get("x-session-id") or None, int(stamp) if stamp.isdigit() else None)
            produced += 1
    finally:
        connection.close()


# ------------------------------------------------------------------------------------------------------------------- run


def _pixel_format(channels: int) -> str:
    return {1: "GRAY", 3: "RGB", 4: "RGBA"}.get(channels, "GRAY")


def _safe_id(text: str | None, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._:-]", "-", text or "")[:64]
    return cleaned or fallback


def run_loopback(
    items: Iterator[tuple[Frame, str, float, str | None]],
    config: LoopbackConfig,
    source_id: str,
    limit: int,
    recorder: LatencyRecorder | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run the detector over `items` (frame, frame id, capture instant in epoch seconds, session id) and publish nothing but the messages
    of the contract: the answer lists, per frame, its `FrameMeta` and its `DetectionFrame`. At most `limit` frames."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RUN_FRAMES:
        raise LoopbackError(f"frames must be an integer from 1 to {MAX_RUN_FRAMES}")
    model = config.detector.identity().to_dict()
    published: list[dict[str, Any]] = []
    total = 0
    for sequence, (frame, frame_id, captured_at, session_id) in enumerate(items):
        if sequence >= limit:
            break
        stamps = {"capture": clock()}
        capture_ts = iso_utc(captured_at)
        try:
            meta = build_frame_meta(frame_id, capture_ts, frame.width, frame.height, _pixel_format(frame.channels),
                                    session_id=session_id, source_id=source_id, sequence=sequence)
            stamps["preprocess"] = clock()
            detections = detect(frame, config.detector)
            stamps["inference"] = clock()
            result = build_detection_frame(frame_id, capture_ts, "pixel", detections, model=model, session_id=session_id)
            stamps["postprocess"] = clock()
        except ContractError as error:
            raise LoopbackError(f"frame {frame_id} does not fit the contract: {error}") from None
        stamps["publish"] = clock()
        if recorder is not None:
            try:
                recorder.record(f"loopback-{source_id}"[:64], stamps)
            except LatencyError:
                pass
        total += len(detections)
        published.append({"meta": meta, "detections": result})
    return {"simulated": True, "pipeline": "loopback", "source": source_id, "frames": len(published), "detections": total, "model": model,
            "messages": published}


def source_items(source: FrameSource, limit: int, wall_clock: Callable[[], float] = time.time) -> Iterator[tuple[Frame, str, float, str | None]]:
    """The frames of a configured source, each with an id built from the source name and its index and the instant it was read."""
    try:
        for frame in source.frames(limit):
            yield frame, _safe_id(f"{source.name}-{frame.index}", f"frame-{frame.index}"), wall_clock(), None
    except SourceError as error:
        raise LoopbackError(str(error)) from None


def stream_items(settings: StreamerSettings, limit: int, decoder: Decoder = pillow_decoder,
                 wall_clock: Callable[[], float] = time.time) -> Iterator[tuple[Frame, str, float, str | None]]:
    """The frames of the MJPEG stream of VISION-STREAMER, with the id, session and capture instant the stream announces (the time of
    reading when it announces none)."""
    for number, part in enumerate(read_stream(settings, limit)):
        frame = decoder(part.jpeg)
        captured = part.capture_ts_ms / 1000.0 if part.capture_ts_ms is not None else wall_clock()
        yield frame, _safe_id(part.frame_id, f"stream-{number}"), captured, _safe_id(part.session_id, "") or None
