# =============================================================================
# HYDRA-UMC-VISION-NODE - src/hydra_umc_vision_node/vision_sim.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Simulated capture and inference: the configuration of the simulation and the loop that runs it.

There is NO real inference and NO real camera here. A frame comes from a simulated source (sources.py); the "model" is a
deterministic function of the frame bytes and the model name (a hash decides how many detections it reports and how long
each stage lasts, around the `simLatencyMs` the model declares). The stage instants are on a virtual clock, so a run
gives the same numbers every time and takes no real time; they go into the existing LatencyRecorder, so `GET /latency`
shows them like those of a real pipeline. What the numbers say is only that the plumbing works.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .latency import LatencyRecorder
from .hardware import HardwareConfig, HardwareConfigError, parse_hardware_config
from .loopback import LoopbackConfig, LoopbackError, parse_loopback_config
from .models import HostCapabilities, ModelError, ModelSpec, parse_host, parse_models
from .registry_gate import GateError, RegistryConfig, parse_registry_config
from .sources import DEFAULT_CHUNK_BYTES, MIN_CHUNK_BYTES, MAX_SOURCE_FRAME_BYTES, FrameSource, RunLimitReached, SourceError, build_source

MAX_CONFIG_BYTES = 256 * 1024
MAX_SOURCES = 16
MAX_RUN_FRAMES = 1000
FRAME_PERIOD_MS = 33.0
# Wall-clock budget of one POST /inference/simulate run when the configuration does not say otherwise
DEFAULT_MAX_RUN_SECONDS = 10.0
MAX_RUN_SECONDS_LIMIT = 3600.0


class VisionConfigError(ValueError):
    """The configuration of the simulation cannot be used."""


@dataclass(frozen=True)
class VisionConfig:
    host: HostCapabilities
    models: tuple[ModelSpec, ...]
    sources: dict[str, FrameSource]
    max_run_seconds: float = DEFAULT_MAX_RUN_SECONDS
    chunk_bytes: int = DEFAULT_CHUNK_BYTES
    # both optional blocks are off unless the file says `"enabled": true` (loopback.py, registry_gate.py)
    loopback: LoopbackConfig | None = None
    registry: RegistryConfig | None = None
    # which device nodes count as a camera / an accelerator (hardware.py); None keeps the defaults
    hardware: HardwareConfig | None = None

    def model(self, name: str) -> ModelSpec | None:
        return next((m for m in self.models if m.name == name), None)


def _reject_constant(name: str) -> float:
    raise ValueError(f"{name} is not a valid JSON number")


def parse_limits(limits: object) -> tuple[float, int]:
    """`limits` is optional: {"maxRunSeconds": number, "chunkBytes": integer}. `maxRunSeconds` is the wall-clock time one
    simulation run may take; `chunkBytes` is the size of the pieces a frame is produced in (the time is checked between
    two pieces, so a run overshoots the limit by one piece at most). Zero, negative, non-finite or non-numeric values
    refuse the whole configuration instead of silently meaning "no limit"."""
    if limits is None:
        return DEFAULT_MAX_RUN_SECONDS, DEFAULT_CHUNK_BYTES
    if not isinstance(limits, dict) or set(limits) - {"maxRunSeconds", "chunkBytes"}:
        raise VisionConfigError("limits is an object with maxRunSeconds and chunkBytes only")
    value = limits.get("maxRunSeconds", DEFAULT_MAX_RUN_SECONDS)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= MAX_RUN_SECONDS_LIMIT:
        raise VisionConfigError(f"limits.maxRunSeconds must be a number above 0 and at most {MAX_RUN_SECONDS_LIMIT:g}")
    chunk = limits.get("chunkBytes", DEFAULT_CHUNK_BYTES)
    if isinstance(chunk, bool) or not isinstance(chunk, int) or not MIN_CHUNK_BYTES <= chunk <= MAX_SOURCE_FRAME_BYTES:
        raise VisionConfigError(f"limits.chunkBytes must be an integer from {MIN_CHUNK_BYTES} to {MAX_SOURCE_FRAME_BYTES}")
    return float(value), chunk


def parse_vision_config(config: object, base: Path) -> VisionConfig:
    if not isinstance(config, dict) or set(config) - {"host", "models", "sources", "limits", "loopback", "registry", "hardware"}:
        raise VisionConfigError("the configuration is an object with host, models, sources and (optional) limits, loopback, registry and hardware")
    max_run_seconds, chunk_bytes = parse_limits(config.get("limits"))
    try:
        host = parse_host(config.get("host", {}))
        models = parse_models(config.get("models", []))
        raw_sources = config.get("sources", [])
        if not isinstance(raw_sources, list) or len(raw_sources) > MAX_SOURCES:
            raise VisionConfigError(f"sources must be a list of at most {MAX_SOURCES} sources")
        sources: dict[str, FrameSource] = {}
        for item in raw_sources:
            source = build_source(item, base)
            if source.name in sources:
                raise VisionConfigError(f"source names must be unique: {source.name}")
            sources[source.name] = source
        loopback = parse_loopback_config(config.get("loopback"))
        registry = parse_registry_config(config.get("registry"))
        hardware = parse_hardware_config(config.get("hardware"))
    except (ModelError, SourceError, LoopbackError, GateError, HardwareConfigError) as error:
        raise VisionConfigError(str(error)) from None
    return VisionConfig(host, models, sources, max_run_seconds, chunk_bytes, loopback, registry, hardware)


def load_vision_config(path: str | Path) -> VisionConfig:
    file = Path(path)
    try:
        if file.stat().st_size > MAX_CONFIG_BYTES:
            raise VisionConfigError(f"the configuration file is larger than {MAX_CONFIG_BYTES} bytes")
        config = json.loads(file.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except OSError as error:
        raise VisionConfigError(f"the configuration file cannot be read: {error.strerror}") from None
    except ValueError as error:
        if isinstance(error, VisionConfigError):
            raise
        raise VisionConfigError(f"the configuration file is not valid JSON: {error}") from None
    return parse_vision_config(config, file.resolve().parent)


def _frames_of(source: FrameSource, limit: int, checkpoint: Callable[[], None], chunk_bytes: int):
    """The frames of `source`, handing it the checkpoint and the piece size when it accepts them (older custom sources
    that only take `limit` are checked between frames only)."""
    parameters: Mapping[str, object]
    try:
        parameters = inspect.signature(source.frames).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "checkpoint" in parameters and "chunk_bytes" in parameters:
        return source.frames(limit, checkpoint=checkpoint, chunk_bytes=chunk_bytes)
    return source.frames(limit)


def run_simulation(
    source: FrameSource,
    model: ModelSpec,
    recorder: LatencyRecorder,
    frames: int,
    max_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> dict:
    """Run `frames` frames of `source` through the fake `model`, recording the stage instants of each in `recorder`.

    `max_seconds` (wall-clock, measured with `clock`; None = no limit) is checked between two frames AND, inside a frame,
    between two pieces of `chunk_bytes` of its generation: the summary of the frames completed so far comes back with
    `truncated: true` and the reason, and a frame cut in the middle is dropped (never recorded, never counted). A run
    overshoots the limit by one piece at most (and by the hashing of one finished frame); a frame that fits in one piece
    is never interrupted, as before. When the limit is reached before the first frame is complete the summary has
    `frames: 0` and no totals."""
    if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= MAX_RUN_FRAMES:
        raise VisionConfigError(f"frames must be an integer from 1 to {MAX_RUN_FRAMES}")
    detections = 0
    count = 0
    total_ms: list[float] = []
    roll = hashlib.blake2b(digest_size=8)
    truncated = False
    started = clock()

    def checkpoint() -> None:
        if max_seconds is not None and clock() - started >= max_seconds:
            raise RunLimitReached

    try:
        for frame in _frames_of(source, frames, checkpoint, chunk_bytes):
            digest = hashlib.blake2b(frame.data + model.name.encode(), digest_size=8).digest()
            roll.update(digest)
            unit = [byte / 255.0 for byte in digest]
            start = frame.index * FRAME_PERIOD_MS / 1000.0
            pre = start + (0.5 + 1.5 * unit[0]) / 1000.0
            inf = pre + model.sim_latency_ms * (0.9 + 0.2 * unit[1]) / 1000.0
            post = inf + (0.2 + 0.3 * unit[2]) / 1000.0
            pub = post + (0.1 + 0.2 * unit[3]) / 1000.0
            durations = recorder.record(source.name, {"capture": start, "preprocess": pre, "inference": inf, "postprocess": post, "publish": pub})
            total_ms.append(durations["total"])
            detections += digest[4] % 4
            count += 1
            if max_seconds is not None and count < frames and clock() - started >= max_seconds:
                truncated = True
                break
    except RunLimitReached:
        truncated = True
    except SourceError as error:
        raise VisionConfigError(str(error)) from None
    if count == 0 and not truncated:
        raise VisionConfigError(f"source {source.name} holds no frames")
    ordered = sorted(total_ms)
    summary = {
        "simulated": True,
        "source": source.name,
        "model": model.name,
        "device": model.device,
        "frames": count,
        "detections": detections,
        "outputDigest": roll.hexdigest(),
        "totalMs": {"mean": sum(total_ms) / count, "max": ordered[-1]} if count else None,
        "truncated": truncated,
    }
    if truncated:
        summary["requestedFrames"] = frames
        summary["truncatedReason"] = f"the run reached the time limit of {max_seconds:g} s after {count} of {frames} frames"
    return summary
